"""speedyapply/2027-*-College-Jobs README tables (USA internships)."""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta

import httpx

from ..models import Posting
from .base import SourceError

LISTS: dict[str, tuple[str, str]] = {
    "speedyapply-swe": ("https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md", "Software"),
    "speedyapply-ai": ("https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/README.md", "AI/ML/Data"),
}

_HEADING = re.compile(r"^#{2,3}\s+(.+?)\s*$")
_HREF = re.compile(r'href="([^"]+)"')
_STRONG = re.compile(r"<strong>(.*?)</strong>")
_TAG = re.compile(r"<[^>]+>")
_PLUS_N = re.compile(r"\s*\+\d+$")
_AGE = re.compile(r"^(\d+)d$")


def _text(cell: str) -> str:
    return html.unescape(_TAG.sub("", cell)).strip()


def parse(markdown: str, source: str, default_category: str, now: datetime) -> list[Posting]:
    postings: list[Posting] = []
    section = ""
    header: list[str] | None = None
    saw_header = False
    for line in markdown.splitlines():
        if m := _HEADING.match(line):
            section, header = m.group(1), None
            continue
        if not line.startswith("| "):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if cells[0] == "Company":
            header, saw_header = cells, True
            continue
        if header is None or len(cells) != len(header):
            continue
        col = dict(zip(header, cells))
        href = _HREF.search(col.get("Posting", ""))
        if not href:
            continue
        strong = _STRONG.search(col["Company"])
        company = html.unescape(strong.group(1)).strip() if strong else _text(col["Company"])
        age = _AGE.match(col.get("Age", ""))
        postings.append(
            Posting(
                company=company,
                title=_text(col["Position"]),
                url=html.unescape(href.group(1)),
                source=source,
                locations=(_PLUS_N.sub("", _text(col["Location"])), "USA"),  # README lists are USA-only
                category="Quant" if section.startswith("Quant") else default_category,
                posted_at=now - timedelta(days=int(age.group(1))) if age else None,
            )
        )
    if not saw_header:
        raise SourceError(f"{source}: no job table found (format changed?)")
    if not postings:
        raise SourceError(f"{source}: table header found but 0 rows parsed (format changed?)")
    return postings


def fetch(client: httpx.Client, now: datetime, *, source: str) -> list[Posting]:
    url, category = LISTS[source]
    resp = client.get(url)
    resp.raise_for_status()
    return parse(resp.text, source, category, now)
