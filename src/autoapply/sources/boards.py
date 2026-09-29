"""Company job boards via public Greenhouse / Lever / Ashby APIs (internship titles only)."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from ..models import Posting
from .base import SourceError

INTERN_RE = re.compile(r"\bintern(ship)?s?\b|\bco-?op\b|\bsummer 20\d\d\b", re.IGNORECASE)


def _get_json(client: httpx.Client, url: str) -> object:
    resp = client.get(url)
    resp.raise_for_status()
    return resp.json()


def fetch_greenhouse(client: httpx.Client, now: datetime, *, token: str, name: str) -> list[Posting]:
    data = _get_json(client, f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
    if not isinstance(data, dict) or "jobs" not in data:
        raise SourceError(f"greenhouse:{token}: unexpected response")
    return [
        Posting(
            company=name,
            title=j["title"].strip(),
            url=j["absolute_url"],
            source=f"board:greenhouse:{token}",
            locations=((j.get("location") or {}).get("name", ""),),
            posted_at=datetime.fromisoformat(j["first_published"]) if j.get("first_published") else None,
        )
        for j in data["jobs"]
        if INTERN_RE.search(j["title"])
    ]


def fetch_lever(client: httpx.Client, now: datetime, *, token: str, name: str) -> list[Posting]:
    data = _get_json(client, f"https://api.lever.co/v0/postings/{token}?mode=json")
    if not isinstance(data, list):
        raise SourceError(f"lever:{token}: unexpected response (unknown board?)")
    out = []
    for j in data:
        if not INTERN_RE.search(j.get("text", "")):
            continue
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
        out.append(
            Posting(
                company=name,
                title=j["text"].strip(),
                url=j["hostedUrl"],
                source=f"board:lever:{token}",
                locations=tuple(locs),
                posted_at=datetime.fromtimestamp(j["createdAt"] / 1000, tz=timezone.utc) if j.get("createdAt") else None,
            )
        )
    return out


def fetch_ashby(client: httpx.Client, now: datetime, *, token: str, name: str) -> list[Posting]:
    data = _get_json(client, f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    if not isinstance(data, dict) or "jobs" not in data:
        raise SourceError(f"ashby:{token}: unexpected response")
    out = []
    for j in data["jobs"]:
        if not j.get("isListed", True) or not INTERN_RE.search(j.get("title", "")):
            continue
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        out.append(
            Posting(
                company=name,
                title=j["title"].strip(),
                url=j["jobUrl"],
                source=f"board:ashby:{token}",
                locations=tuple(loc for loc in locs if loc),
                posted_at=datetime.fromisoformat(j["publishedAt"]) if j.get("publishedAt") else None,
            )
        )
    return out
