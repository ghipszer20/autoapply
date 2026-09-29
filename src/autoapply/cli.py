"""Command line: autoapply [--db PATH] [--config PATH] {discover,on,off,status}."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
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
    for name, fetch in build_sources(cfg, conn):
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


ROOT = Path(__file__).resolve().parents[2]


def _status(conn: sqlite3.Connection) -> None:
    c = db.counts(conn)
    today = datetime.now().astimezone().date()
    d = db.digest(conn, today)["counts"]
    print(f"enabled: {'yes' if db.is_enabled(conn) else 'no'}")
    print(f"last discover: {db.get_state(conn, 'last_discover', 'never')}")
    print(f"postings: {c['total']} total, {c['eligible']} eligible")
    print("today: " + (", ".join(f"{k} {v}" for k, v in sorted(d.items())) or "nothing yet"))


def _digest(conn: sqlite3.Connection, day) -> None:
    d = db.digest(conn, day)
    print(f"{day}: " + (", ".join(f"{k} {v}" for k, v in sorted(d["counts"].items())) or "no activity"))
    for r in d["rows"]:
        print(f"  {r['status']:9} {r['company']} | {r['title']} | {(r['reason'] or '')[:100]}")


def _cmd_run(args, conn) -> int:
    from .runner import Locked, RunLock, discover_now, log_report, run_cycle

    from .runner import CycleReport

    live = not args.dry_run
    try:
        cfg = load_config(args.config)
        with RunLock():
            rep = run_cycle(cfg, conn, live=live, limit=args.limit, ats=args.ats, headed=args.headed,
                            discover_fn=None if args.no_discover else (lambda: discover_now(cfg, conn)))
    except Locked as e:
        print(f"skipped: {e}")
        return 0
    except Exception:  # noqa: BLE001 - under pythonw nobody sees stderr: the log is the only trace
        import traceback

        rep = CycleReport(status="crash", lines=traceback.format_exc().splitlines()[-12:])
        log_report(rep, datetime.now().astimezone())
        print("\n".join(rep.lines))
        return 1
    path = log_report(rep, datetime.now().astimezone())
    print("\n".join(rep.lines) or rep.status)
    print(f"log: {path}")
    return 0


def _cmd_apply(args, conn) -> int:
    """One posting by key, for testing adapters: dry run unless --live."""
    import json

    from playwright.sync_api import sync_playwright

    from .apply import apply_one
    from .resume import render
    from .runner import _open_browser, adapters, build_deps

    cfg = load_config(args.config)
    row = conn.execute("SELECT * FROM postings WHERE key = ?", (args.key,)).fetchone()
    if row is None:
        print(f"unknown key {args.key}")
        return 1
    holder = {"key": args.key}
    deps = build_deps(cfg, conn, holder)
    with sync_playwright() as p:
        render.use_playwright(p)
        ctx, close = _open_browser(p, live=args.live, headed=args.headed or args.live)
        try:
            page = ctx.new_page()
            adapter = adapters()[row["ats"]]
            if args.code and hasattr(adapter, "email_code"):
                adapter.email_code = lambda: args.code
            if args.wait_code and hasattr(adapter, "email_code"):
                code_file = ROOT / "data" / "security_code.txt"
                code_file.unlink(missing_ok=True)

                def wait_for_code():
                    import time

                    print("WAITING FOR CODE in data/security_code.txt", flush=True)
                    for _ in range(180):
                        if code_file.exists() and code_file.read_text().strip():
                            return code_file.read_text().strip()
                        time.sleep(5)
                    return None
                adapter.email_code = wait_for_code
            o = apply_one(page, adapter, key=row["key"], url=row["url"], company=row["company"],
                          title=row["title"], terms=tuple(json.loads(row["terms"])), deps=deps, live=args.live,
                          locations=tuple(json.loads(row["locations"])))
        finally:
            close()
    db.record_application(conn, row["key"], o.status, o.reason, now=datetime.now().astimezone(),
                          resume_path=o.resume_path, screenshot=o.screenshot, form_url=o.form_url, answers=o.answers)
    print(f"{o.status}: {o.reason}")
    for fid, a in o.answers.items():
        print(f"  {a['source']:9} {fid[:30]:30} {str(a['value'])[:90]}")
    print(f"screenshot: {o.screenshot}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="autoapply")
    ap.add_argument("--db", default=str(ROOT / "autoapply.db"))
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("discover", "on", "off", "status", "manual", "gaps", "gmail-auth"):
        sub.add_parser(name)
    r = sub.add_parser("run", help="one bounded pass (live unless --dry-run; live requires 'on')")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--limit", type=int)
    r.add_argument("--ats", nargs="*")
    r.add_argument("--headed", action="store_true")
    r.add_argument("--no-discover", action="store_true")
    a = sub.add_parser("apply", help="one posting by key (dry run unless --live)")
    a.add_argument("key")
    a.add_argument("--live", action="store_true")
    a.add_argument("--headed", action="store_true")
    a.add_argument("--code", help="emailed security code to enter if the form asks for one")
    a.add_argument("--wait-code", action="store_true",
                   help="if a security code is asked for, wait up to 15 min for it in data/security_code.txt")
    d = sub.add_parser("digest")
    d.add_argument("--date")
    rt = sub.add_parser("retry", help="forget outcomes with this status so they are tried again")
    rt.add_argument("status", choices=["skipped", "failed", "deferred", "manual"])
    sc = sub.add_parser("schedule", help="Windows Task Scheduler entry")
    sc.add_argument("action", choices=["install", "remove", "show"])
    args = ap.parse_args(argv)
    conn = db.connect(args.db)
    cmd = args.command
    if cmd == "on":
        db.set_state(conn, "enabled", "1")
        print("autoapply enabled")
    elif cmd == "off":
        db.set_state(conn, "enabled", "0")
        print("autoapply disabled")
    elif cmd == "status":
        _status(conn)
    elif cmd == "digest":
        from datetime import date as _date

        _digest(conn, _date.fromisoformat(args.date) if args.date else datetime.now().astimezone().date())
    elif cmd == "manual":
        for row in db.manual_list(conn):
            print(f"{row['company']} | {row['title']} | {row['url']} | {(row['reason'] or '')[:80]}")
    elif cmd == "gaps":
        print("Most common reasons applications were skipped (add answers to profile.yaml / answer_bank.yaml):")
        for reason, n in db.top_skip_reasons(conn):
            print(f"  {n:4}  {reason}")
    elif cmd == "gmail-auth":
        from .sources.email_alerts import authorize

        authorize()
        print("gmail authorized (read-only); job-alert emails are now a discovery source")
    elif cmd == "retry":
        print(f"reset {db.reset_status(conn, args.status)} {args.status} applications")
    elif cmd == "schedule":
        from .schedule import schedule_cmd

        return schedule_cmd(args.action)
    elif cmd == "run":
        return _cmd_run(args, conn)
    elif cmd == "apply":
        return _cmd_apply(args, conn)
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
