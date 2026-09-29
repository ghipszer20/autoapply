"""Command line: autoapply [--db PATH] [--config PATH] {discover,on,off,status}."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from typing import TextIO

import httpx

from . import db
from .config import Config, ConfigError, load_config
from .filter import evaluate
from .sources.base import SourceError
from .sources.registry import build_sources

USER_AGENT = "autoapply/0.1 (personal job search; github.com/ghipszer20)"


def discover(conn: sqlite3.Connection, cfg: Config, client: httpx.Client, now: datetime, out: TextIO) -> int:
    ok = 0
    for name, fetch in build_sources(cfg):
        try:
            postings = fetch(client, now)
        except (httpx.HTTPError, SourceError, ValueError, KeyError) as e:
            print(f"  ! {name}: {type(e).__name__}: {e}", file=out)
            continue
        stats = db.upsert_postings(conn, postings, now)
        ok += 1
        print(f"  {name}: {len(postings)} postings ({stats.new} new)", file=out)
    db.set_eligibility(conn, [(key, *evaluate(p, cfg.filter)) for key, p in db.iter_postings(conn)])
    db.set_state(conn, "last_discover", now.isoformat())
    c = db.counts(conn)
    print(f"total {c['total']}, eligible {c['eligible']}", file=out)
    print("eligible by ATS: " + ", ".join(f"{k} {v}" for k, v in c["eligible_by_ats"].items()), file=out)
    print("top rejects: " + ", ".join(f"{r} {n}" for r, n in c["top_rejects"]), file=out)
    return 0 if ok else 1


def _status(conn: sqlite3.Connection) -> None:
    c = db.counts(conn)
    print(f"enabled: {'yes' if db.is_enabled(conn) else 'no'}")
    print(f"last discover: {db.get_state(conn, 'last_discover', 'never')}")
    print(f"postings: {c['total']} total, {c['eligible']} eligible")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="autoapply")
    ap.add_argument("--db", default="autoapply.db")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("command", choices=["discover", "on", "off", "status"])
    args = ap.parse_args(argv)
    conn = db.connect(args.db)
    if args.command == "on":
        db.set_state(conn, "enabled", "1")
        print("autoapply enabled")
    elif args.command == "off":
        db.set_state(conn, "enabled", "0")
        print("autoapply disabled")
    elif args.command == "status":
        _status(conn)
    else:
        try:
            cfg = load_config(args.config)
        except ConfigError as e:
            print(f"config error: {e}", file=sys.stderr)
            return 2
        headers = {"User-Agent": USER_AGENT}
        with httpx.Client(timeout=60, follow_redirects=True, headers=headers) as client:
            return discover(conn, cfg, client, datetime.now(timezone.utc), sys.stdout)
    return 0
