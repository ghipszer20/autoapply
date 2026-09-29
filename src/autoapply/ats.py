"""Identify which application system (ATS) a URL belongs to and derive a dedupe key."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

THIRD_PARTY_HOSTS = (
    "linkedin.com",
    "indeed.com",
    "joinhandshake.com",
    "handshake.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "simplify.jobs",
)
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "ref", "src", "source", "mobile", "needsredirect", "embed", "ats",
    "lever-source", "lever-origin",
}
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


@dataclass(frozen=True)
class AtsRef:
    ats: str
    key: str
    canonical_url: str


def canonicalize(url: str) -> str:
    parts = urlsplit(url.strip())
    query = sorted((k, v) for k, v in parse_qsl(parts.query) if k.lower() not in TRACKING_PARAMS)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def classify(url: str) -> AtsRef:
    canonical = canonicalize(url)
    parts = urlsplit(canonical)
    host, path = parts.netloc, parts.path
    query = dict(parse_qsl(parts.query))

    if "gh_jid" in query and query["gh_jid"].isdigit():
        return AtsRef("greenhouse", f"greenhouse:{query['gh_jid']}", canonical)
    if host.endswith("greenhouse.io"):
        if m := re.match(r"^/[^/]+/jobs/(\d+)", path):
            return AtsRef("greenhouse", f"greenhouse:{m.group(1)}", canonical)
        if query.get("token", "").isdigit():
            return AtsRef("greenhouse", f"greenhouse:{query['token']}", canonical)
    if host == "jobs.lever.co" and (m := re.match(rf"^/([^/]+)/({_UUID})", path)):
        return AtsRef("lever", f"lever:{m.group(2)}", f"https://jobs.lever.co/{m.group(1)}/{m.group(2)}")
    if host == "jobs.ashbyhq.com" and (m := re.match(rf"^/([^/]+)/({_UUID})", path)):
        return AtsRef("ashby", f"ashby:{m.group(2)}", f"https://jobs.ashbyhq.com/{m.group(1)}/{m.group(2)}")
    if host.endswith(".myworkdayjobs.com") and "/job/" in path:
        tenant = host.split(".")[0]
        return AtsRef("workday", f"workday:{tenant}:{path.rsplit('_', 1)[-1]}", canonical)
    if host.endswith("myworkdaysite.com") and (m := re.match(r"^/recruiting/([^/]+)/.*/job/", path)):
        return AtsRef("workday", f"workday:{m.group(1)}:{path.rsplit('_', 1)[-1]}", canonical)
    if host.endswith(".icims.com") and (m := re.match(r"^/jobs/(\d+)", path)):
        return AtsRef("icims", f"icims:{host.split('.')[0]}:{m.group(1)}", canonical)
    if host.endswith("smartrecruiters.com") and (m := re.match(r"^/[^/]+/(\d+)", path)):
        return AtsRef("smartrecruiters", f"smartrecruiters:{m.group(1)}", canonical)
    if any(host == h or host.endswith("." + h) for h in THIRD_PARTY_HOSTS):
        return AtsRef("third_party", f"url:{canonical}", canonical)
    return AtsRef("other", f"url:{canonical}", canonical)
