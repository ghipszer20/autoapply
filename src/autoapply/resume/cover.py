"""Cover letter from resume facts only (profile policy: whenever a cover-letter field is present)."""

from __future__ import annotations

import re
from datetime import date
from html import escape
from pathlib import Path

from pydantic import BaseModel

from ..llm import LLMError
from .model import Resume, to_text
from .render import html_to_pdf
from .verify import verify_text


class CoverLetter(BaseModel):
    paragraphs: list[str]


SYSTEM = (
    "Write a concise internship cover letter (3 short paragraphs, under 250 words) for the candidate in RESUME, "
    "for the JOB. Use only facts from RESUME: never add skills, tools, numbers, metrics or experiences that are not "
    "there, and do not claim experience with the job's technologies unless RESUME shows it. Plain, specific, no "
    "cliches. No greeting or sign-off lines; paragraphs only."
)


def write_cover_letter(llm, master: Resume, *, company: str, title: str, description: str, out_dir: Path,
                       today: date | None = None) -> Path | None:
    """Return the PDF path, or None when drafting or the truth check fails (caller treats it as unanswerable)."""
    today = today or date.today()
    master_text = to_text(master)
    try:
        letter = llm.ask(f"RESUME:\n{master_text}\n\nJOB: {title} at {company}\n{description[:5000]}",
                         CoverLetter, purpose="cover", system=SYSTEM)
    except LLMError:
        return None
    body = "\n".join(letter.paragraphs)
    allowed = f"{master_text}\n{company}\n{title}\n{today.isoformat()}"
    if not letter.paragraphs or verify_text(allowed, body):
        return None
    contact = " | ".join(master.contact)
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><style>@page{size:Letter;margin:0.9in}"
        "body{font-family:'Times New Roman',serif;font-size:11.5pt;line-height:1.4}p{margin:0 0 10pt}</style></head><body>"
        f"<p><b>{escape(master.name)}</b><br>{escape(contact)}</p><p>{today.strftime('%B %d, %Y').replace(' 0', ' ')}</p>"
        f"<p>Hiring Team, {escape(company)}<br>Re: {escape(title)}</p><p>Dear Hiring Team,</p>"
        + "".join(f"<p>{escape(p)}</p>" for p in letter.paragraphs)
        + f"<p>Sincerely,<br>{escape(master.name)}</p></body></html>"
    )
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{company}_{title}").strip("_")[:80]
    out = out_dir / f"CoverLetter_{slug}_{today.isoformat()}.pdf"
    return out if html_to_pdf(html, out) >= 1 else None
