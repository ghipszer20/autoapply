"""One bounded scheduled pass: (maybe) discover, then apply to up to N eligible postings."""

from __future__ import annotations

import os
import random
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from . import db
from .adapters.ashby import AshbyAdapter
from .adapters.greenhouse import GreenhouseAdapter
from .answers import load_bank
from .apply import ApplyDeps, ApplyOutcome, apply_one
from .config import Config
from .llm import LLM
from .profile import Profile
from .resume.model import load_resume, to_text
from .resume.select import ResumeSettings

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "data" / "run.lock"
LOCK_STALE = timedelta(hours=2)


def adapters() -> dict:
    from .sources.email_alerts import greenhouse_security_code

    out = {"greenhouse": GreenhouseAdapter(email_code=greenhouse_security_code), "ashby": AshbyAdapter()}
    from .adapters.lever import LeverAdapter
    from .adapters.workday import WorkdayAdapter

    out["lever"] = LeverAdapter()
    from .sources.email_alerts import workday_verification_link

    # pilot: only used when "workday" is in run.ats_enabled or passed via --ats
    out["workday"] = WorkdayAdapter(email_code=workday_verification_link)
    from .adapters.generic import HOSTS, GenericAdapter

    generic = GenericAdapter()
    out.update({name: generic for name in HOSTS.values()})
    return out


class Locked(Exception):
    pass


class RunLock:
    """Task Scheduler fires every 30 min; a long pass must not overlap the next one."""

    def __init__(self, path: Path = LOCK):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            age = datetime.now() - datetime.fromtimestamp(self.path.stat().st_mtime)
            if age < LOCK_STALE:
                raise Locked(f"another run holds {self.path} (pid {self.path.read_text().strip()}, {age} old)")
        self.path.write_text(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


@dataclass
class CycleReport:
    status: str = "ok"
    lines: list[str] = field(default_factory=list)
    outcomes: list[tuple[str, ApplyOutcome]] = field(default_factory=list)

    def add(self, line: str) -> None:
        self.lines.append(line)


def build_deps(cfg: Config, conn: sqlite3.Connection, key_holder: dict) -> ApplyDeps:
    master = load_resume(ROOT / cfg.resume.master_yaml)
    llm = LLM(conn=conn, model=cfg.llm.model, daily_total=cfg.llm.daily_total, per_purpose=dict(cfg.llm.per_purpose),
              purpose_models=dict(cfg.llm.purpose_models))
    return ApplyDeps(
        profile=Profile.load(ROOT / "profile.yaml"),
        master=master,
        master_text=to_text(master),
        resume_settings=ResumeSettings(master_pdf=Path(cfg.resume.master_pdf), master_yaml=ROOT / cfg.resume.master_yaml,
                                       out_dir=Path(cfg.resume.out_dir), tailor_threshold=cfg.resume.tailor_threshold),
        bank=load_bank(ROOT / "answer_bank.yaml"),
        llm=llm,
        applied_before=lambda company: db.applied_before(conn, company, exclude_key=key_holder.get("key", "")),
        screenshot_dir=ROOT / "screenshots",
        cover_dir=Path(cfg.resume.out_dir) / "cover_letters",
    )


def _open_browser(p, *, live: bool, headed: bool):
    """Live runs use real Chrome with a persistent profile (cookies, fewer bot flags); dry runs are headless."""
    if live:
        ctx = p.chromium.launch_persistent_context(
            str(ROOT / "browser_profile"), channel="chrome", headless=False, viewport={"width": 1280, "height": 900},
            args=["--start-minimized"])  # stays out of the way when a scheduled run starts
        return ctx, ctx.close
    browser = p.chromium.launch(headless=not headed)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    return ctx, browser.close


def run_cycle(cfg: Config, conn: sqlite3.Connection, *, live: bool, limit: int | None = None,
              ats: list[str] | None = None, headed: bool = False, discover_fn: Callable | None = None,
              now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
              sleep: Callable[[float], None] = time.sleep) -> CycleReport:
    rep = CycleReport()
    if live and not db.is_enabled(conn):
        rep.status = "disabled"
        rep.add("autoapply is off (kill switch); nothing submitted")
        return rep
    last = db.get_state(conn, "last_discover")
    if discover_fn and (not last or now() - datetime.fromisoformat(last) > timedelta(hours=cfg.run.discover_every_hours)):
        try:
            discover_fn()
            rep.add("discovered new postings")
        except Exception as e:  # noqa: BLE001 - stale postings are still worth applying to
            rep.add(f"discover failed ({type(e).__name__}: {str(e)[:120]}); using known postings")
    today = now().date()
    room = cfg.run.daily_cap - db.submitted_on(conn, today) if live else 10**6
    target = min(limit or cfg.run.per_cycle, room)
    if target <= 0:
        rep.status = "cap"
        rep.add(f"daily cap {cfg.run.daily_cap} reached")
        return rep
    all_adapters = adapters()
    use_ats = [a for a in (ats or cfg.run.ats_enabled) if a in all_adapters]
    cands = db.candidates(conn, use_ats, limit=target * 6, skip_dry_run=not live)
    if not cands:
        rep.add("no eligible postings left for " + ", ".join(use_ats))
        return rep

    from playwright.sync_api import sync_playwright

    from .resume import render

    key_holder: dict = {}
    deps = build_deps(cfg, conn, key_holder)
    per_company: Counter = Counter()
    attempted = examined = 0
    with sync_playwright() as p:
        render.use_playwright(p)
        ctx, close = _open_browser(p, live=live, headed=headed)
        try:
            for row in cands:
                if attempted >= target or examined >= target * 6:
                    break
                company = row["company"]
                if per_company[company.lower()] >= cfg.run.per_company_per_cycle:
                    continue
                if live and not db.is_enabled(conn):  # kill switch is checked before every submission
                    rep.add("turned off mid-run; stopping")
                    break
                examined += 1
                key_holder["key"] = row["key"]
                page = ctx.new_page()
                try:
                    import json

                    outcome = apply_one(page, all_adapters[row["ats"]], key=row["key"], url=row["url"],
                                        company=company, title=row["title"], terms=tuple(json.loads(row["terms"])),
                                        deps=deps, live=live, today=today,
                                        locations=tuple(json.loads(row["locations"])))
                except Exception as e:  # noqa: BLE001 - one bad page must not end the pass
                    outcome = ApplyOutcome("failed", f"crash: {type(e).__name__}: {str(e)[:200]}")
                finally:
                    page.close()
                budget_hit = "budget" in outcome.reason and "llm" in outcome.reason
                status = "deferred" if outcome.status == "skipped" and outcome.reason.startswith("llm error") \
                    else outcome.status
                db.record_application(conn, row["key"], status, outcome.reason, now=now(),
                                      resume_path=outcome.resume_path, screenshot=outcome.screenshot,
                                      form_url=outcome.form_url, answers=outcome.answers)
                rep.outcomes.append((row["key"], outcome))
                rep.add(f"{status:9} {company} | {row['title']} | {outcome.reason[:140]}")
                if status in ("skipped", "deferred", "closed"):
                    if budget_hit:
                        rep.add("LLM daily budget used up; stopping")
                        break
                    continue
                per_company[company.lower()] += 1
                attempted += 1
                if live and attempted < target:
                    sleep(random.uniform(*cfg.run.spacing_seconds))
        finally:
            close()
            render.use_playwright(None)
    return rep


def log_report(rep: CycleReport, when: datetime) -> Path:
    path = ROOT / "logs" / f"run-{when.date().isoformat()}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"== {when.isoformat(timespec='seconds')} [{rep.status}]\n")
        fh.writelines(line + "\n" for line in rep.lines)
    return path


def discover_now(cfg: Config, conn: sqlite3.Connection) -> None:
    import io

    from .cli import USER_AGENT, discover

    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        discover(conn, cfg, client, datetime.now().astimezone(), io.StringIO())
