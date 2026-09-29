"""SQLite store: one row per unique job (keyed by ats.classify), plus key/value state."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .ats import classify
from .models import Posting

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    key           TEXT PRIMARY KEY,
    ats           TEXT NOT NULL,
    company       TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    sources       TEXT NOT NULL,   -- JSON list, discovery order
    locations     TEXT NOT NULL,   -- JSON list
    terms         TEXT NOT NULL,   -- JSON list
    category      TEXT NOT NULL,
    degrees       TEXT NOT NULL,   -- JSON list
    sponsorship   TEXT NOT NULL,
    posted_at     TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    eligible      INTEGER,         -- NULL until filtered
    reject_reason TEXT
);
CREATE TABLE IF NOT EXISTS state (
    name  TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    key          TEXT PRIMARY KEY,   -- postings.key
    status       TEXT NOT NULL,      -- submitted | dry_run | skipped | manual | failed
    reason       TEXT,
    resume_path  TEXT,
    screenshot   TEXT,
    form_url     TEXT,
    answers      TEXT,               -- JSON audit of every submitted answer and its source
    attempts     INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL,
    submitted_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    ts     TEXT NOT NULL,
    key    TEXT,
    kind   TEXT NOT NULL,
    detail TEXT
);
"""
FINAL_STATUSES = ("submitted", "skipped", "manual")
MAX_ATTEMPTS = 2


@dataclass
class UpsertStats:
    new: int = 0
    updated: int = 0


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _union(a: Iterable[str], b: Iterable[str]) -> list[str]:
    return list(dict.fromkeys([*a, *b]))


def upsert_postings(conn: sqlite3.Connection, postings: Iterable[Posting], now: datetime) -> UpsertStats:
    stats = UpsertStats()
    ts = now.isoformat()
    for p in postings:
        ref = classify(p.url)
        row = conn.execute("SELECT * FROM postings WHERE key = ?", (ref.key,)).fetchone()
        posted = p.posted_at.isoformat() if p.posted_at else None
        if row is None:
            conn.execute(
                "INSERT INTO postings (key, ats, company, title, url, sources, locations, terms, category,"
                " degrees, sponsorship, posted_at, first_seen, last_seen)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ref.key, ref.ats, p.company, p.title, ref.canonical_url, json.dumps([p.source]),
                 json.dumps(list(p.locations)), json.dumps(list(p.terms)), p.category,
                 json.dumps(list(p.degrees)), p.sponsorship, posted, ts, ts),
            )
            stats.new += 1
            continue
        ats, url = row["ats"], row["url"]
        if ats == "third_party" and ref.ats != "third_party":  # company's own site wins
            ats, url = ref.ats, ref.canonical_url
        elif ats == "other" and ref.ats not in ("other", "third_party"):  # classifier learned this host
            ats = ref.ats
        conn.execute(
            "UPDATE postings SET ats = ?, url = ?, sources = ?, locations = ?, terms = ?, category = ?,"
            " degrees = ?, sponsorship = ?, posted_at = COALESCE(posted_at, ?), last_seen = ? WHERE key = ?",
            (ats, url,
             json.dumps(_union(json.loads(row["sources"]), [p.source])),
             json.dumps(_union(json.loads(row["locations"]), p.locations)),
             json.dumps(_union(json.loads(row["terms"]), p.terms)),
             row["category"] or p.category,
             json.dumps(_union(json.loads(row["degrees"]), p.degrees)),
             row["sponsorship"] or p.sponsorship,
             posted, ts, ref.key),
        )
        stats.updated += 1
    conn.commit()
    return stats


def iter_postings(conn: sqlite3.Connection) -> Iterator[tuple[str, Posting]]:
    for row in conn.execute("SELECT * FROM postings ORDER BY first_seen, key"):
        yield row["key"], Posting(
            company=row["company"],
            title=row["title"],
            url=row["url"],
            source=",".join(json.loads(row["sources"])),
            locations=tuple(json.loads(row["locations"])),
            terms=tuple(json.loads(row["terms"])),
            category=row["category"],
            degrees=tuple(json.loads(row["degrees"])),
            sponsorship=row["sponsorship"],
            posted_at=datetime.fromisoformat(row["posted_at"]) if row["posted_at"] else None,
        )


def set_eligibility(conn: sqlite3.Connection, results: Iterable[tuple[str, bool, str]]) -> None:
    conn.executemany(
        "UPDATE postings SET eligible = ?, reject_reason = ? WHERE key = ?",
        [(int(ok), reason or None, key) for key, ok, reason in results],
    )
    conn.commit()


def get_state(conn: sqlite3.Connection, name: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM state WHERE name = ?", (name,)).fetchone()
    return row["value"] if row else default


def set_state(conn: sqlite3.Connection, name: str, value: str) -> None:
    conn.execute("INSERT INTO state (name, value) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                 (name, value))
    conn.commit()


def is_enabled(conn: sqlite3.Connection) -> bool:
    return get_state(conn, "enabled", "0") == "1"


def counts(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
    eligible = conn.execute("SELECT COUNT(*) FROM postings WHERE eligible = 1").fetchone()[0]
    by_ats = dict(conn.execute(
        "SELECT ats, COUNT(*) FROM postings WHERE eligible = 1 GROUP BY ats ORDER BY COUNT(*) DESC").fetchall())
    rejects = [tuple(r) for r in conn.execute(
        "SELECT reject_reason, COUNT(*) FROM postings WHERE eligible = 0"
        " GROUP BY reject_reason ORDER BY COUNT(*) DESC LIMIT 8")]
    return {"total": total, "eligible": eligible, "eligible_by_ats": by_ats, "top_rejects": rejects}


def record_application(conn: sqlite3.Connection, key: str, status: str, reason: str, *, now: datetime,
                       resume_path: str = "", screenshot: str = "", form_url: str = "",
                       answers: dict | None = None) -> None:
    ts = now.isoformat()
    conn.execute(
        "INSERT INTO applications (key, status, reason, resume_path, screenshot, form_url, answers, attempts,"
        " updated_at, submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET status = excluded.status, reason = excluded.reason,"
        " resume_path = excluded.resume_path, screenshot = excluded.screenshot, form_url = excluded.form_url,"
        " answers = excluded.answers, attempts = applications.attempts + 1, updated_at = excluded.updated_at,"
        " submitted_at = COALESCE(applications.submitted_at, excluded.submitted_at)",
        (key, status, reason, resume_path, screenshot, form_url, json.dumps(answers or {}, default=str), ts,
         ts if status == "submitted" else None),
    )
    conn.execute("INSERT INTO events (ts, key, kind, detail) VALUES (?, ?, ?, ?)", (ts, key, status, reason))
    conn.commit()


def candidates(conn: sqlite3.Connection, ats: Iterable[str], limit: int) -> list[sqlite3.Row]:
    """Eligible postings not yet finished, newest first. Failed ones get MAX_ATTEMPTS tries; dry runs don't count."""
    ats = list(ats)
    marks = ",".join("?" * len(ats))
    final = ",".join("?" * len(FINAL_STATUSES))
    return conn.execute(
        f"SELECT p.* FROM postings p LEFT JOIN applications a ON a.key = p.key"
        f" WHERE p.eligible = 1 AND p.ats IN ({marks})"
        f" AND (a.key IS NULL OR (a.status NOT IN ({final}) AND NOT (a.status = 'failed' AND a.attempts >= ?)))"
        f" ORDER BY COALESCE(p.posted_at, p.first_seen) DESC, p.key LIMIT ?",
        (*ats, *FINAL_STATUSES, MAX_ATTEMPTS, limit),
    ).fetchall()


def applied_before(conn: sqlite3.Connection, company: str, *, exclude_key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM applications a JOIN postings p ON p.key = a.key"
        " WHERE a.status = 'submitted' AND lower(p.company) = lower(?) AND a.key != ? LIMIT 1",
        (company, exclude_key),
    ).fetchone()
    return row is not None


def submitted_on(conn: sqlite3.Connection, day: date) -> int:
    return conn.execute("SELECT COUNT(*) FROM applications WHERE status = 'submitted' AND substr(submitted_at, 1, 10) = ?",
                        (day.isoformat(),)).fetchone()[0]


def digest(conn: sqlite3.Connection, day: date) -> dict:
    rows = conn.execute(
        "SELECT a.status, a.reason, p.company, p.title, p.url FROM applications a JOIN postings p ON p.key = a.key"
        " WHERE substr(a.updated_at, 1, 10) = ? ORDER BY a.updated_at", (day.isoformat(),)).fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"counts": counts, "rows": rows}


def manual_list(conn: sqlite3.Connection, limit: int = 200) -> list[sqlite3.Row]:
    """Needs a human: CAPTCHA/verification/failed applications, plus eligible postings only on third-party sites."""
    return conn.execute(
        "SELECT p.company, p.title, p.url, a.reason FROM applications a JOIN postings p ON p.key = a.key"
        " WHERE a.status IN ('manual', 'failed')"
        " UNION ALL SELECT p.company, p.title, p.url, 'third-party site only' FROM postings p"
        " LEFT JOIN applications a ON a.key = p.key WHERE p.ats = 'third_party' AND p.eligible = 1 AND a.key IS NULL"
        " LIMIT ?", (limit,)).fetchall()


def top_skip_reasons(conn: sqlite3.Connection, limit: int = 25) -> list[tuple[str, int]]:
    return [tuple(r) for r in conn.execute(
        "SELECT reason, COUNT(*) FROM applications WHERE status = 'skipped' GROUP BY reason"
        " ORDER BY COUNT(*) DESC LIMIT ?", (limit,))]


def reset_status(conn: sqlite3.Connection, status: str) -> int:
    n = conn.execute("DELETE FROM applications WHERE status = ?", (status,)).rowcount
    conn.commit()
    return n
