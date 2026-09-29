"""SimplifyJobs/Summer2027-Internships listings.json (all terms, incl. off-season)."""

from __future__ import annotations

import html
from datetime import datetime, timezone

import httpx

from ..models import Posting
from .base import SourceError

URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"


def parse(items: object) -> list[Posting]:
    if not isinstance(items, list) or not items:
        raise SourceError("simplify: expected a non-empty JSON list (format changed?)")
    out: list[Posting] = []
    for x in items:
        if not x.get("active") or not x.get("is_visible", True) or not x.get("url"):
            continue
        ts = x.get("date_posted")
        out.append(
            Posting(
                company=html.unescape(x.get("company_name", "")).strip(),
                title=html.unescape(x.get("title", "")).strip(),
                url=x["url"],
                source="simplify",
                locations=tuple(x.get("locations") or ()),
                terms=tuple(x.get("terms") or ()),
                category=x.get("category") or "",
                degrees=tuple(x.get("degrees") or ()),
                sponsorship=x.get("sponsorship") or "",
                posted_at=datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
            )
        )
    return out


def fetch(client: httpx.Client, now: datetime) -> list[Posting]:
    resp = client.get(URL)
    resp.raise_for_status()
    return parse(resp.json())
