"""Hand-off mode for bot-checked sites: automation fills the form, the user passes the check and clicks Submit.

Never clicks Submit and never touches CAPTCHAs. It re-fills from the answers stored when the application was
prepared (same text, no new LLM call), waits for the user, and records the result when the confirmation page shows.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from . import db
from .adapters.base import AdapterError, PostingClosed
from .answers import AnswerContext, Resolved, resolve
from .config import Config
from .forms import FormSpec

CONFIRMED = re.compile(r"thank you for (applying|your application)|thanks for applying|application (was |has been )?"
                       r"(successfully )?(submitted|received)|successfully submitted|we('ve| have) received your application",
                       re.I)
WAIT_SECONDS = 600


def stored_answers(raw: str | None, spec: FormSpec) -> dict[str, Resolved]:
    """Answers saved when the application was prepared, limited to fields still on the form."""
    ids = {f.id for f in spec.fields}
    out = {}
    for fid, a in json.loads(raw or "{}").items():
        if fid in ids:
            value = Path(a["value"]) if a.get("source") == "file" else a["value"]
            out[fid] = Resolved(value, a.get("source", "stored"))
    return out


def missing_required(spec: FormSpec, answers: dict[str, Resolved]) -> FormSpec:
    """Required fields the stored answers don't cover (the posting changed since it was prepared)."""
    fields = [f for f in spec.fields if f.required and f.id not in answers]
    return FormSpec(company=spec.company, title=spec.title, url=spec.url, fields=fields,
                    description=spec.description, meta=spec.meta)


def confirmed(page) -> bool:
    try:
        return "confirmation" in page.url or "/thanks" in page.url or bool(CONFIRMED.search(page.inner_text("body")))
    except Exception:  # noqa: BLE001 - navigation in progress
        return False


def wait_for_user(page, skip: threading.Event, *, seconds: int = WAIT_SECONDS,
                  sleep: Callable[[int], None] | None = None) -> str:
    """'submitted' when the confirmation shows; 'skipped' if the user skips, closes the tab, or time runs out."""
    sleep = sleep or (lambda ms: page.wait_for_timeout(ms))
    for _ in range(seconds):
        if page.is_closed() or skip.is_set():
            return "skipped"
        if confirmed(page):
            return "submitted"
        sleep(1000)
    return "skipped"


def run_assist(cfg: Config, conn: sqlite3.Connection, *, limit: int, out: Callable[[str], None] = print,
               read_line: Callable[[], str] = input) -> dict[str, int]:
    from playwright.sync_api import sync_playwright

    from .resume import render
    from .runner import _open_browser, adapters, build_deps

    queue = db.assist_queue(conn, limit)
    if not queue:
        out("Nothing waiting for you.")
        return {}
    counts: dict[str, int] = {}
    holder: dict = {}
    deps = build_deps(cfg, conn, holder)
    all_adapters = adapters()
    with sync_playwright() as p:
        render.use_playwright(p)
        ctx, close = _open_browser(p, live=True, headed=True)
        try:
            for i, row in enumerate(queue, 1):
                holder["key"] = row["key"]
                page = ctx.new_page()
                page.bring_to_front()
                label = f"[{i}/{len(queue)}] {row['company']} | {row['title']}"
                status, reason = _assist_one(page, all_adapters[row["ats"]], row, deps, cfg, out, read_line, label)
                db.record_application(conn, row["key"], status, reason, now=datetime.now().astimezone(),
                                      answers=json.loads(row["answers"] or "{}"))
                counts[status] = counts.get(status, 0) + 1
                out(f"    -> {status}: {reason}")
                if not page.is_closed():
                    page.close()
        finally:
            close()
            render.use_playwright(None)
    return counts


def _assist_one(page, adapter, row, deps, cfg, out, read_line, label) -> tuple[str, str]:
    try:
        spec = adapter.load(page, row["url"], row["key"], row["company"], row["title"])
    except PostingClosed as e:
        return "closed", str(e)
    except AdapterError as e:
        return "assist", f"could not load the form this time: {e}"
    spec.meta["terms"] = tuple(json.loads(row["terms"]))
    spec.meta["locations"] = tuple(json.loads(row["locations"]))
    answers = stored_answers(row["answers"], spec)
    gap = missing_required(spec, answers)
    if gap.fields:  # the form gained questions since it was prepared
        res = resolve(gap, AnswerContext(profile=deps.profile, resume_pdf=deps.resume_settings.master_pdf,
                                         resume_text=deps.master_text, bank=deps.bank, llm=deps.llm,
                                         applied_before=deps.applied_before))
        if not res.ok:
            return "skipped", f"new required question: {res.skip_reason}"
        answers.update(res.answers)
    problems = adapter.fill(page, spec, answers)
    if problems:
        out(f"{label}\n    filled with problems (check them in the window): {'; '.join(problems[:3])}")
    else:
        out(f"{label}\n    all {len(answers)} answers filled.")
    out("    Your turn in Chrome: pass the check if one appears and click Submit. Press Enter here to skip.")
    skip = threading.Event()
    threading.Thread(target=lambda: (read_line(), skip.set()), daemon=True).start()
    status = wait_for_user(page, skip)
    return (status, "confirmed after you submitted") if status == "submitted" else \
        ("assist", "skipped for now; still waiting for you")
