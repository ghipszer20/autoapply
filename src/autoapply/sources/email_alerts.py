"""LinkedIn / Indeed / Handshake job-alert emails (Gmail API, read-only) -> Postings on the company's own ATS.

No bots on those sites: we only read the alert emails the user already receives. Each alert job is resolved to
the company's own application system (known posting, or a Greenhouse/Lever/Ashby board lookup); anything that
can't be resolved keeps its third-party link and lands on the manual list, never auto-submitted.

Setup (once, by the user): put an OAuth "Desktop app" client at data/gmail_credentials.json, then run
`autoapply gmail-auth`. Until then this source is simply not registered.
"""

from __future__ import annotations

import base64
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx

from ..forms import norm
from ..models import Posting
from .base import SourceError

ROOT = Path(__file__).resolve().parents[3]
CREDENTIALS = ROOT / "data" / "gmail_credentials.json"
TOKEN = ROOT / "data" / "gmail_token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
API = "https://gmail.googleapis.com/gmail/v1/users/me"
QUERY = ("newer_than:{days}d (from:linkedin.com OR from:indeed.com OR from:indeedemail.com OR from:joinhandshake.com "
         "OR from:handshake.com) (job OR jobs OR intern OR internship)")

LINK_PATTERNS = {
    "linkedin": re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)"),
    "indeed": re.compile(r"indeed\.com/(?:rc/clk|viewjob|pagead/clk|m/viewjob)[^\"'\s]*?[?&]jk=([0-9a-f]+)"),
    "handshake": re.compile(r"joinhandshake\.com/(?:stu/)?(?:jobs|job-search)/(\d+)"),
}


@dataclass(frozen=True)
class AlertJob:
    source: str
    job_id: str
    title: str
    company: str
    location: str
    link: str


class _Blocks(HTMLParser):
    """Flatten HTML into text blocks, remembering the href of the anchor a block sits in."""

    BLOCK = {"p", "div", "td", "tr", "li", "h1", "h2", "h3", "h4", "table", "br", "span"}

    def __init__(self):
        super().__init__()
        self.blocks: list[tuple[str, str | None]] = []
        self._buf: list[str] = []
        self._href: list[str | None] = []

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        if text:
            self.blocks.append((text, self._href[-1] if self._href else None))
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._flush()
            self._href.append(dict(attrs).get("href"))
        elif tag in self.BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag == "a":
            self._flush()
            if self._href:
                self._href.pop()
        elif tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


_NOISE = re.compile(r"^(view job|apply( now)?|see all jobs|new|actively recruiting|promoted|easy apply|\d+ (applicants?|"
                    r"connections?)|be an early applicant|save)$", re.I)


def parse_alert(source: str, html: str) -> list[AlertJob]:
    p = _Blocks()
    p.feed(html)
    p.close()
    rx = LINK_PATTERNS[source]
    jobs: dict[str, AlertJob] = {}
    blocks = p.blocks
    for i, (text, href) in enumerate(blocks):
        m = rx.search(href or "")
        if not m or _NOISE.match(text) or m.group(1) in jobs:
            continue
        rest = [t for t, _ in blocks[i + 1:i + 5] if not _NOISE.match(t)]
        company, location = "", ""
        if rest:
            parts = re.split(r"\s+[·•|–-]\s+", rest[0], maxsplit=1)
            company = parts[0]
            location = parts[1] if len(parts) > 1 else (rest[1] if len(rest) > 1 else "")
        if not company:
            continue
        jobs[m.group(1)] = AlertJob(source, m.group(1), text, company.strip(), location.strip(), href)
    return list(jobs.values())


def _sender_source(sender: str) -> str | None:
    s = sender.lower()
    for name in ("linkedin", "indeed", "handshake"):
        if name in s:
            return name
    return None


# --- resolution to the company's own ATS --------------------------------------------------------------------------


def _title_tokens(t: str) -> set[str]:
    return set(norm(t).split()) - {"intern", "internship", "2027", "summer", "the", "and", "of", "-"}


def _same_title(a: str, b: str) -> bool:
    ta, tb = _title_tokens(a), _title_tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.6


def _slugs(company: str) -> list[str]:
    words = re.sub(r"[^a-z0-9 ]+", "", company.lower().replace("&", " and ")).split()
    words = [w for w in words if w not in ("inc", "llc", "corp", "corporation", "co", "ltd", "the")]
    out = ["".join(words), "-".join(words), words[0] if words else ""]
    return [s for s in dict.fromkeys(out) if s]


def _board_lookup(client: httpx.Client, company: str, title: str) -> str | None:
    for slug in _slugs(company):
        try:
            r = client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
            if r.status_code == 200:
                for j in r.json().get("jobs", []):
                    if _same_title(j["title"], title):
                        return j["absolute_url"]
            r = client.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
            if r.status_code == 200 and isinstance(r.json(), list):
                for j in r.json():
                    if _same_title(j.get("text", ""), title):
                        return j["hostedUrl"]
            r = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
            if r.status_code == 200:
                for j in r.json().get("jobs", []):
                    if _same_title(j.get("title", ""), title):
                        return j["jobUrl"]
        except (httpx.HTTPError, ValueError):
            continue
    return None


def resolve(job: AlertJob, conn: sqlite3.Connection | None, client: httpx.Client) -> Posting | None:
    """None when the job is already known from another source; otherwise a Posting (company ATS or third-party)."""
    if conn is not None:
        for row in conn.execute("SELECT title FROM postings WHERE lower(company) = lower(?) AND ats != 'third_party'",
                                (job.company,)):
            if _same_title(row["title"], job.title):
                return None
    url = _board_lookup(client, job.company, job.title) or job.link
    return Posting(company=job.company, title=job.title, url=url, source=f"alert:{job.source}",
                   locations=(job.location,) if job.location else ())


# --- Gmail API -----------------------------------------------------------------------------------------------------


def credentials():
    """Refreshed OAuth credentials, or None when Gmail isn't set up yet."""
    if not TOKEN.exists():
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return creds if creds.valid else None


def authorize() -> None:
    """Interactive, run once by the user: opens a browser for Google consent (read-only Gmail)."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CREDENTIALS.exists():
        raise SystemExit(f"Put your OAuth Desktop-app client JSON at {CREDENTIALS} first (see README).")
    creds = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), SCOPES).run_local_server(port=0)
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(creds.to_json(), encoding="utf-8")


def _html_of(payload: dict) -> str:
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"] + "==").decode("utf-8", "replace")
    for part in payload.get("parts") or []:
        if html := _html_of(part):
            return html
    return ""


def gmail_get(client: httpx.Client, token: str, path: str, **params) -> dict:
    r = client.get(f"{API}/{path}", params=params, headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json()


def recent_messages(client: httpx.Client, token: str, query: str, limit: int = 50) -> list[dict]:
    ids = gmail_get(client, token, "messages", q=query, maxResults=limit).get("messages", [])
    return [gmail_get(client, token, f"messages/{m['id']}", format="full") for m in ids]


def fetch(client: httpx.Client, now: datetime, *, conn: sqlite3.Connection | None = None, days: int = 3) -> list[Posting]:
    creds = credentials()
    if creds is None:
        raise SourceError("gmail not authorized (run `autoapply gmail-auth`)")
    out: list[Posting] = []
    for msg in recent_messages(client, creds.token, QUERY.format(days=days)):
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        src = _sender_source(headers.get("from", ""))
        if not src:
            continue
        for job in parse_alert(src, _html_of(msg.get("payload", {}))):
            if (p := resolve(job, conn, client)) is not None:
                out.append(p)
    return out


_VERIFY_LINK = re.compile(r"https://[a-z0-9.-]*myworkday(?:jobs|site)?\.com/[^\s\"'<>]*(?:verify|activate|confirm)[^\s\"'<>]*",
                          re.I)


def workday_verification_link(tenant: str, *, wait_seconds: int = 90, sleep=None) -> str | None:
    """Poll Gmail for the tenant's 'verify your account' email and return its link (None if Gmail isn't set up)."""
    import time

    sleep = sleep or time.sleep
    creds = credentials()
    if creds is None:
        return None
    with httpx.Client(timeout=30) as client:
        for _ in range(max(1, wait_seconds // 10)):
            for msg in recent_messages(client, creds.token, "newer_than:1h (from:myworkday.com OR from:workday.com)", 10):
                m = _VERIFY_LINK.search(_html_of(msg.get("payload", {})))
                if m and tenant.lower() in m.group(0).lower():
                    return m.group(0).replace("&amp;", "&")
            sleep(10)
    return None


_CODE_CONTEXT = re.compile(r"(?:security|verification) code(.{0,240})", re.I | re.S)


def find_security_code(text: str) -> str | None:
    """First 8-character token after 'security code' that looks like a code (has a digit or mixed case)."""
    for ctx in _CODE_CONTEXT.findall(text):
        for tok in re.findall(r"\b[A-Za-z0-9]{8}\b", ctx):
            if re.search(r"\d", tok) or (re.search(r"[a-z]", tok) and re.search(r"[A-Z]", tok)):
                return tok
    return None


def greenhouse_security_code(*, wait_seconds: int = 90, sleep=None) -> str | None:
    """Poll Gmail for Greenhouse's 8-character application security code (None if Gmail isn't set up)."""
    import time

    sleep = sleep or time.sleep
    creds = credentials()
    if creds is None:
        return None
    with httpx.Client(timeout=30) as client:
        for _ in range(max(1, wait_seconds // 10)):
            for msg in recent_messages(client, creds.token, "newer_than:15m from:greenhouse", 5):
                body = re.sub(r"<[^>]+>", " ", _html_of(msg.get("payload", {})) or msg.get("snippet", ""))
                if code := find_security_code(body):
                    return code
            sleep(10)
    return None
