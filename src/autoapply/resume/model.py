"""Structured master resume (resume.yaml)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class Bullet(BaseModel):
    id: str
    text: str


class Entry(BaseModel):
    id: str
    org: str
    location: str = ""
    title: str = ""
    dates: str = ""
    bullets: list[Bullet] = []


class Section(BaseModel):
    id: str
    heading: str
    entries: list[Entry]


class Education(BaseModel):
    id: str
    org: str
    location: str = ""
    title: str = ""
    dates: str = ""
    gpa: str = ""
    coursework: list[str] = []
    skills: list[str] = []


class Resume(BaseModel):
    verified: bool = False
    name: str
    contact: list[str]
    education: Education
    sections: list[Section]


def load_resume(path: str | Path) -> Resume:
    return Resume.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8-sig")))


def to_text(r: Resume) -> str:
    e = r.education
    lines = [r.name, " | ".join(r.contact), "", "EDUCATION", f"{e.org}, {e.location}", f"{e.title} ({e.dates})"]
    if e.gpa:
        lines.append(f"GPA: {e.gpa}")
    if e.coursework:
        lines.append("Relevant Coursework: " + ", ".join(e.coursework))
    if e.skills:
        lines.append("Technical Skills: " + ", ".join(e.skills))
    for s in r.sections:
        lines += ["", s.heading.upper()]
        for en in s.entries:
            head = " | ".join(x for x in (en.org, en.title, en.location, en.dates) if x)
            lines.append(head)
            lines += [f"- {b.text}" for b in en.bullets]
    return "\n".join(lines)
