"""One application, end to end: load form -> choose resume -> resolve answers -> fill -> screenshot -> submit."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .adapters.base import Adapter, AdapterError
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


def _audit(answers) -> dict[str, Any]:
    out = {}
    for fid, a in answers.items():
        v = a.value
        out[fid] = {"value": str(v) if isinstance(v, Path) else v, "source": a.source}
    return out


def apply_one(page, adapter: Adapter, *, key: str, url: str, company: str, title: str, terms: tuple[str, ...],
              deps: ApplyDeps, live: bool, today: date | None = None) -> ApplyOutcome:
    today = today or date.today()
    try:
        spec = adapter.load(page, url, key, company, title)
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
    shot = deps.screenshot_dir / today.isoformat() / f"{key.replace(':', '_')}_{'live' if live else 'dry'}.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(shot), full_page=True)
    base["screenshot"] = str(shot)
    if problems:
        return ApplyOutcome("failed", "fill: " + "; ".join(problems[:4]), **base)
    if not live:
        return ApplyOutcome("dry_run", f"resume: {choice.reason}", **base)
    result = adapter.submit(page)
    done = deps.screenshot_dir / today.isoformat() / f"{key.replace(':', '_')}_after_submit.png"
    page.screenshot(path=str(done), full_page=True)
    base["screenshot"] = str(done)
    return ApplyOutcome(result.status, result.detail, **base)


def outcome_json(o: ApplyOutcome) -> str:
    return json.dumps({"status": o.status, "reason": o.reason, "resume": o.resume_path, "screenshot": o.screenshot,
                       "at": datetime.now().isoformat(timespec="seconds")})
