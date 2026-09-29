"""Ask the LLM to select, reorder and reword master resume content for a posting; build the tailored Resume."""

from __future__ import annotations

import json

from pydantic import BaseModel

from .model import Bullet, Resume, to_text


class TailorError(Exception):
    pass


class TailoredEntry(BaseModel):
    id: str
    bullets: list[Bullet]  # id = the master bullet it rewords


class TailoredSection(BaseModel):
    id: str
    entries: list[TailoredEntry]


class Tailoring(BaseModel):
    section_order: list[str]
    sections: list[TailoredSection]
    skills: list[str]
    coursework: list[str]


SYSTEM = (
    "You tailor a one-page student resume to a job posting. You may ONLY: reorder sections, entries and bullets; "
    "drop entries or bullets; choose which skills and coursework to list (exact strings from the master); and "
    "reword a bullet to use the posting's vocabulary where it describes the SAME work. Never add a skill, "
    "technology, tool, number, metric, employer, title, date or claim that is not in the master. Every bullet keeps "
    "the id of the master bullet it came from. Keep it to one page: at most the master's bullet count."
)


def _master_json(m: Resume) -> str:
    return json.dumps({
        "skills": m.education.skills,
        "coursework": m.education.coursework,
        "sections": [{"id": s.id, "heading": s.heading, "entries": [
            {"id": e.id, "org": e.org, "title": e.title, "bullets": [b.model_dump() for b in e.bullets]}
            for e in s.entries]} for s in m.sections],
    }, indent=1)


def request_tailoring(llm, master: Resume, company: str, title: str, description: str) -> Tailoring:
    prompt = (f"MASTER (ids are binding):\n{_master_json(master)}\n\nJOB: {title} at {company}\n"
              f"{description[:6000]}")
    return llm.ask(prompt, Tailoring, purpose="tailor", system=SYSTEM)


def apply_tailoring(master: Resume, t: Tailoring) -> Resume:
    """Build the tailored resume. Headers (org/title/dates) are always copied from the master."""
    msections = {s.id: s for s in master.sections}
    by_id = {s.id: s for s in t.sections}
    order = [sid for sid in t.section_order if sid in by_id] + [s.id for s in t.sections if s.id not in t.section_order]
    out = master.model_copy(deep=True)
    out.sections = []
    for sid in order:
        if sid not in msections:
            raise TailorError(f"unknown section {sid}")
        ms = msections[sid]
        mentries = {e.id: e for e in ms.entries}
        new = ms.model_copy(deep=True)
        new.entries = []
        for te in by_id[sid].entries:
            if te.id not in mentries:
                raise TailorError(f"unknown entry {te.id}")
            e = mentries[te.id].model_copy(deep=True)
            e.bullets = [Bullet(id=b.id, text=b.text.strip()) for b in te.bullets if b.text.strip()]
            new.entries.append(e)
        out.sections.append(new)
    out.education.skills = list(t.skills)
    out.education.coursework = list(t.coursework)
    return out


__all__ = ["Tailoring", "TailoredEntry", "TailoredSection", "TailorError", "apply_tailoring", "request_tailoring",
           "to_text"]
