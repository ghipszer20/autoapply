"""Pick the resume for one application: the master PDF, or a tailored one that passed every check."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from ..llm import LLMError
from .fit import fit_score
from .model import load_resume, to_text
from .render import RenderError, render_pdf
from .tailor import TailorError, apply_tailoring, request_tailoring
from .verify import verify


@dataclass
class ResumeSettings:
    master_pdf: Path
    master_yaml: Path
    out_dir: Path
    tailor_threshold: int = 70


@dataclass
class ResumeChoice:
    path: Path
    tailored: bool
    reason: str
    fit: int | None = None


def _slug(s: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", s)).strip("_")[:60]


def choose_resume(settings: ResumeSettings, *, company: str, title: str, description: str, llm,
                  today: date | None = None) -> ResumeChoice:
    master_pdf = settings.master_pdf
    if not description.strip() or llm is None:
        return ResumeChoice(master_pdf, False, "no description or no LLM")
    try:
        master = load_resume(settings.master_yaml)
    except (OSError, ValidationError, ValueError) as e:
        return ResumeChoice(master_pdf, False, f"resume.yaml unreadable: {e}")
    if not master.verified:
        return ResumeChoice(master_pdf, False, "resume.yaml not verified by user; tailoring off")
    try:
        fit = fit_score(llm, to_text(master), company, title, description)
        if fit.score >= settings.tailor_threshold:
            return ResumeChoice(master_pdf, False, f"fit {fit.score} >= {settings.tailor_threshold}", fit.score)
        tailored = apply_tailoring(master, request_tailoring(llm, master, company, title, description))
    except (LLMError, TailorError) as e:
        return ResumeChoice(master_pdf, False, f"tailoring failed: {e}")
    problems = verify(master, tailored)
    if problems:
        return ResumeChoice(master_pdf, False, "verify failed: " + "; ".join(problems[:5]), fit.score)
    out = settings.out_dir / f"{_slug(company)}_{_slug(title)}_{(today or date.today()).isoformat()}.pdf"
    try:
        render_pdf(tailored, out)
    except (RenderError, OSError) as e:
        return ResumeChoice(master_pdf, False, f"render failed: {e}", fit.score)
    return ResumeChoice(out, True, f"tailored (fit {fit.score})", fit.score)
