"""One application, end to end: load form -> choose resume -> resolve answers -> fill -> screenshot -> submit."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .adapters.base import Adapter, AdapterError, PostingClosed, SubmitResult
from .answers import AnswerContext, BankEntry, resolve
from .profile import Profile
from .resume.cover import write_cover_letter
from .resume.model import Resume
from .resume.select import ResumeSettings, choose_resume


@dataclass
class ApplyOutcome:
    status: str  # submitted | dry_run | skipped | manual | failed
    reason: str = ""
    resume_path: str = ""
    screenshot: str = ""
    answers: dict[str, Any] = field(default_factory=dict)
    form_url: str = ""


@dataclass
class ApplyDeps:
    profile: Profile
    master: Resume
    master_text: str
    resume_settings: ResumeSettings
    bank: list[BankEntry]
    llm: Any
    applied_before: Callable[[str], bool]
    screenshot_dir: Path
    cover_dir: Path


def _safe(key: str) -> str:
    """Filesystem-safe name for a posting key ('url:https://...' keys contain slashes)."""
    import re

    return re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:120]


def _audit(answers) -> dict[str, Any]:
    out = {}
    for fid, a in answers.items():
        v = a.value
        out[fid] = {"value": str(v) if isinstance(v, Path) else v, "source": a.source}
    return out


def apply_one(page, adapter: Adapter, *, key: str, url: str, company: str, title: str, terms: tuple[str, ...],
              deps: ApplyDeps, live: bool, today: date | None = None) -> ApplyOutcome:
    today = today or date.today()
    if hasattr(adapter, "apply_flow"):  # multi-page systems (Workday)
        return _apply_flow(page, adapter, key=key, url=url, company=company, title=title, terms=terms, deps=deps,
                           live=live, today=today)
    try:
        spec = adapter.load(page, url, key, company, title)
    except PostingClosed as e:
        return ApplyOutcome("closed", str(e))
    except AdapterError as e:
        return ApplyOutcome("failed", f"load: {e}")
    spec.meta["terms"] = terms
    pre = resolve(spec, AnswerContext(profile=deps.profile, resume_pdf=deps.resume_settings.master_pdf,
                                      resume_text=deps.master_text, bank=deps.bank, llm=None,
                                      applied_before=deps.applied_before, today=today), rules_only=True)
    if not pre.ok:  # decided by rules alone: spend no LLM calls on it
        return ApplyOutcome("skipped", pre.skip_reason or "", form_url=spec.url)
    choice = choose_resume(deps.resume_settings, company=company, title=title, description=spec.description,
                           llm=deps.llm, today=today)

    def cover(s):
        return write_cover_letter(deps.llm, deps.master, company=company, title=title, description=s.description,
                                  out_dir=deps.cover_dir, today=today) if deps.llm is not None else None

    ctx = AnswerContext(profile=deps.profile, resume_pdf=choice.path, resume_text=deps.master_text, bank=deps.bank,
                        llm=deps.llm, applied_before=deps.applied_before, cover_letter=cover, today=today)
    res = resolve(spec, ctx)
    base = dict(resume_path=str(choice.path), answers=_audit(res.answers), form_url=spec.url)
    if not res.ok:
        return ApplyOutcome("skipped", res.skip_reason or "", **base)
    problems = adapter.fill(page, spec, res.answers)
    shot = deps.screenshot_dir / today.isoformat() / f"{_safe(key)}_{'live' if live else 'dry'}.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(shot), full_page=True)
    base["screenshot"] = str(shot)
    if problems:
        return ApplyOutcome("failed", "fill: " + "; ".join(problems[:4]), **base)
    if not live:
        return ApplyOutcome("dry_run", f"resume: {choice.reason}", **base)
    try:
        result = adapter.submit(page)
    except Exception as e:  # noqa: BLE001 - the click may or may not have gone through
        result = SubmitResult("failed", f"submit error: {type(e).__name__}: {str(e)[:150]}")
    done = deps.screenshot_dir / today.isoformat() / f"{_safe(key)}_after_submit.png"
    page.screenshot(path=str(done), full_page=True)
    base["screenshot"] = str(done)
    if result.status == "failed":  # submit was clicked: never retry automatically (could apply twice)
        return ApplyOutcome("manual", f"unconfirmed submit, check screenshot: {result.detail}", **base)
    return ApplyOutcome(result.status, result.detail, **base)


def _apply_flow(page, adapter, *, key, url, company, title, terms, deps: ApplyDeps, live: bool,
                today: date) -> ApplyOutcome:
    shots = deps.screenshot_dir / today.isoformat()
    shots.mkdir(parents=True, exist_ok=True)
    cache: dict = {}

    def screenshot(tag: str) -> str:
        path = shots / f"{_safe(key)}_{tag}.png"
        page.screenshot(path=str(path), full_page=True)
        return str(path)

    def answer(spec):
        spec.meta["terms"] = terms
        if "choice" not in cache:
            cache["choice"] = choose_resume(deps.resume_settings, company=company, title=title,
                                            description=spec.description, llm=deps.llm, today=today)

        def cover(s):
            return write_cover_letter(deps.llm, deps.master, company=company, title=title, description=s.description,
                                      out_dir=deps.cover_dir, today=today) if deps.llm is not None else None

        ctx = AnswerContext(profile=deps.profile, resume_pdf=cache["choice"].path, resume_text=deps.master_text,
                            bank=deps.bank, llm=deps.llm, applied_before=deps.applied_before, cover_letter=cover,
                            today=today)
        return resolve(spec, ctx)

    try:
        out = adapter.apply_flow(page, url=url, key=key, company=company, title=title,
                                 email=deps.profile.get("identity.email"), answer=answer, live=live,
                                 screenshot=screenshot)
    except AdapterError as e:
        return ApplyOutcome("failed", f"flow: {e}")
    resume = cache.get("choice")
    return ApplyOutcome(out["status"], out.get("reason", ""), resume_path=str(resume.path) if resume else "",
                        screenshot=out.get("screenshot", ""), answers=out.get("answers", {}), form_url=page.url)


def outcome_json(o: ApplyOutcome) -> str:
    return json.dumps({"status": o.status, "reason": o.reason, "resume": o.resume_path, "screenshot": o.screenshot,
                       "at": datetime.now().isoformat(timespec="seconds")})
