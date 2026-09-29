from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Posting:
    """One job posting as reported by a single source."""

    company: str
    title: str
    url: str
    source: str
    locations: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()
    category: str = ""
    degrees: tuple[str, ...] = ()
    sponsorship: str = ""
    posted_at: datetime | None = None
