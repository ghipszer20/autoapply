# autoapply Plan 1: Foundation and Discovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `autoapply discover` pulls internship postings from speedyapply (SWE and AI lists), SimplifyJobs, and quant-firm job boards. It removes duplicates by application-system job ID, applies the eligibility filter, and stores the results in SQLite. `autoapply on|off|status` provides the kill switch that the phone will toggle.

**Architecture:**
- Each source is a function `(httpx.Client, now) -> list[Posting]`, isolated so one failing source never blocks the others.
- `ats.classify(url)` turns any posting URL into `(ats, key, canonical_url)`. The key is the dedupe identity, so the same Greenhouse job found in two lists becomes one row.
- Filtering is pure rules driven by `config.yaml`. No LLM is used in this plan.
- Includes two throwaway spikes that confirm the parts of the architecture that rely on the subscription (`claude -p` under Task Scheduler) and on the phone (Remote Control).

**Tech Stack:** Python 3.13, httpx, pydantic v2, PyYAML, sqlite3 (stdlib), pytest. All HTTP in tests goes through `httpx.MockTransport`, so there's no network in unit tests.

**Spec:** `docs/superpowers/specs/2026-09-29-autoapply-design.md`

**Later plans (not in scope here):** a generic `github_lists.py` for more repos (vanshb03/Summer2027-Internships was last updated 2026-08-23, so it's low value right now). Plan 2 LLM + answers + resume tailoring. Plan 3 Greenhouse/Ashby/Lever adapters + runner + Task Scheduler. Plan 4 Gmail alerts. Plan 5 Workday. Plan 6 iCIMS/SmartRecruiters/generic.

## Global Constraints

- Project root: `C:\Users\24GHi\Code\autoapply` (own git repo; home dir `C:\Users\24GHi` is a separate git repo holding secrets — never stage from there).
- Before every commit: `git rev-parse --show-toplevel` must print `C:/Users/24GHi/Code/autoapply`; review `git diff --cached --name-only`.
- `.gitignore` (already committed) covers `.env`, `*.db`, `profile.yaml`, `answer_bank.yaml`, `credentials*.json`, `token*.json`, `data/`, `browser_profile/`, `screenshots/`, `logs/`, `.venv/`.
- GitHub repo `ghipszer20/autoapply` must be **private**. No `.github/workflows/`.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Python command on this machine: `py -3.13` to create the venv, then `.venv/Scripts/python` (Git Bash) for everything else.
- New installs start **disabled** (`enabled` state defaults to off).
- No API keys: `ANTHROPIC_API_KEY` must not be set for `claude -p`, or it bills the API instead of the Max subscription.

## Review Focus

1. **A listing repo changes its markdown format** (renames a column, moves tables). Expected: the source fails loudly with `SourceError` instead of silently returning 0 postings. Pinned in Task 4 (`test_no_table_raises`, `test_header_without_rows_raises`).
2. **The same Greenhouse job appears under three URL shapes** (`job-boards.greenhouse.io/x/jobs/N`, a company site with `?gh_jid=N`, `boards.greenhouse.io/embed/job_app?token=N`). Expected: exactly one DB row. Pinned in Task 2 (`test_classify`) and Task 3 (`test_cross_source_same_job_merges`).
3. **One source times out or returns 500** during discover. Expected: the other sources are still stored, the failure is printed, and the exit code is 0 (1 only if every source failed). Pinned in Task 8 (`test_discover_survives_failing_source`, `test_discover_all_fail_exit_1`).
4. **Re-running discover.** Expected: no duplicate rows, `first_seen` unchanged, and postings that vanish from a source are kept (not deleted). Pinned in Task 3 (`test_reupsert_keeps_first_seen`).
5. **HTML entities in names** (`AT&amp;T`, `Procter &amp; Gamble`) and the speedyapply `"+3"` location suffix. Expected: they're cleaned. Pinned in Task 4 (`test_entities_and_location_suffix`).

---

## File Structure

```
autoapply/
  pyproject.toml
  config.yaml                       # filter rules + board watchlist (committed)
  CLAUDE.md                         # phone-control instructions (Task 9)
  README.md
  docs/spikes/2026-09-29-spikes.md  # spike findings (Task 1, Task 9)
  src/autoapply/
    __init__.py
    __main__.py                     # python -m autoapply
    models.py                       # Posting dataclass
    ats.py                          # classify(url) -> AtsRef; canonicalize(url)
    db.py                           # schema, upsert/merge, eligibility, state
    config.py                       # pydantic Config, load_config()
    filter.py                       # evaluate(posting, FilterConfig) -> (bool, reason)
    cli.py                          # argparse: discover / on / off / status
    sources/
      __init__.py
      base.py                       # SourceError, FetchFn type
      speedyapply.py
      simplify.py
      boards.py                     # greenhouse / lever / ashby public APIs
      registry.py                   # build_sources(cfg) -> list[(name, FetchFn)]
  tests/
    conftest.py
    fixtures/speedyapply_sample.md
    fixtures/simplify_sample.json
    test_ats.py test_db.py test_speedyapply.py test_simplify.py
    test_boards.py test_config.py test_filter.py test_cli.py
```

---

### Task 1: Spike — `claude -p` from Task Scheduler on the subscription (throwaway)

This checks whether Plan 2's "no API key" LLM design can work. Nothing from this task is committed except the findings note.

**Files:**
- Create: `docs/spikes/2026-09-29-spikes.md`

- [ ] **Step 1: Confirm no API key is set** (PowerShell)

```powershell
[bool]$env:ANTHROPIC_API_KEY; [Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY','User'); [Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY','Machine')
```
Expected: `False` and two empty lines. If a key is set anywhere, stop and tell the user. It would make `claude -p` bill the API.

- [ ] **Step 2: Interactive headless call**

```powershell
claude -p 'Reply with exactly this JSON and nothing else: {"ok": true}' --output-format json
```
Expected: a JSON object with `"is_error": false` and `"result"` containing `{"ok": true}`.

- [ ] **Step 3: Same call from Task Scheduler**

```powershell
$claude = (Get-Command claude).Source
$out = "$env:TEMP\autoapply_spike.json"
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"`"$claude`" -p `"Reply with the single word OK`" --output-format json > `"$out`" 2>&1`""
Register-ScheduledTask -TaskName 'autoapply-spike' -Action $action -User $env:USERNAME | Out-Null
Start-ScheduledTask -TaskName 'autoapply-spike'
```
Then poll until `(Get-ScheduledTask autoapply-spike).State -ne 'Running'` (use the Monitor tool with an until-loop, not sleep). Then:
```powershell
Get-Content "$env:TEMP\autoapply_spike.json"; (Get-ScheduledTaskInfo autoapply-spike).LastTaskResult
Unregister-ScheduledTask -TaskName 'autoapply-spike' -Confirm:$false
```
Expected: JSON with `"is_error": false` and `LastTaskResult` 0.

- [ ] **Step 4: Record findings** in `docs/spikes/2026-09-29-spikes.md`:

```markdown
# Spikes 2026-09-29

## A. claude -p under Task Scheduler (subscription auth)
- ANTHROPIC_API_KEY set: <no/yes>
- Interactive `claude -p --output-format json`: <pass/fail + is_error value>
- Task Scheduler run: <pass/fail>, LastTaskResult=<n>
- claude path used: <path>
- Conclusion: <Plan 2 can call claude -p from scheduled runs | needs workaround: ...>

## B. Remote Control phone on/off
(filled in Task 9)
```
If Step 3 fails, stop and report to the user before Task 2. Plan 2's design depends on it.

- [ ] **Step 5: Commit**

```bash
cd /c/Users/24GHi/Code/autoapply && git rev-parse --show-toplevel
git add docs/spikes/2026-09-29-spikes.md && git diff --cached --name-only
git commit -m "docs: record claude -p Task Scheduler spike

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Scaffold, `Posting` model, URL classification, private GitHub repo

**Files:**
- Create: `pyproject.toml`, `src/autoapply/__init__.py`, `src/autoapply/__main__.py`, `src/autoapply/models.py`, `src/autoapply/ats.py`, `tests/conftest.py`, `tests/test_ats.py`

**Interfaces:**
- Produces:
  - `models.Posting` (frozen dataclass: `company: str, title: str, url: str, source: str, locations: tuple[str,...]=(), terms: tuple[str,...]=(), category: str="", degrees: tuple[str,...]=(), sponsorship: str="", posted_at: datetime|None=None`)
  - `ats.AtsRef(ats: str, key: str, canonical_url: str)`
  - `ats.classify(url: str) -> AtsRef`
  - `ats.canonicalize(url: str) -> str`
  - `ats` values: `"greenhouse"|"lever"|"ashby"|"workday"|"icims"|"smartrecruiters"|"third_party"|"other"`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "autoapply"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["httpx>=0.27", "pydantic>=2.7", "pyyaml>=6.0"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
autoapply = "autoapply.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["live: hits the real network (run with -m live)"]
addopts = "-m 'not live'"
```

`src/autoapply/__init__.py`:
```python
"""Automatic internship application pipeline."""
```

`tests/conftest.py`:
```python
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES
```

- [ ] **Step 2: Create the venv and install**

```bash
cd /c/Users/24GHi/Code/autoapply && py -3.13 -m venv .venv && .venv/Scripts/python -m pip install -q -e ".[dev]"
```
Expected: exits 0.

- [ ] **Step 3: Write the failing test** `tests/test_ats.py`

```python
import pytest

from autoapply.ats import canonicalize, classify

CASES = [
    ("https://job-boards.greenhouse.io/schonfeld/jobs/8180089", "greenhouse", "greenhouse:8180089"),
    ("https://boards.greenhouse.io/figma/jobs/6143238004?gh_jid=6143238004", "greenhouse", "greenhouse:6143238004"),
    ("https://app.careerpuck.com/job-board/lyft/job/8767726002?gh_jid=8767726002", "greenhouse", "greenhouse:8767726002"),
    ("https://job-boards.eu.greenhouse.io/imc/jobs/4979079101", "greenhouse", "greenhouse:4979079101"),
    ("https://boards.greenhouse.io/embed/job_app?for=acme&token=5550001", "greenhouse", "greenhouse:5550001"),
    ("https://jobs.lever.co/belvederetrading/10746b3d-1760-4573-9b63-b93f5a5e4fc0", "lever", "lever:10746b3d-1760-4573-9b63-b93f5a5e4fc0"),
    ("https://jobs.lever.co/belvederetrading/10746b3d-1760-4573-9b63-b93f5a5e4fc0/apply?lever-source=x", "lever", "lever:10746b3d-1760-4573-9b63-b93f5a5e4fc0"),
    ("https://jobs.ashbyhq.com/dryft/3f1c261d-9b65-412b-9f17-34b8968bdd78/application", "ashby", "ashby:3f1c261d-9b65-412b-9f17-34b8968bdd78"),
    ("https://biotechne.wd5.myworkdayjobs.com/en-US/Biotechne/job/San-Jose-CA/Hardware-Engineering-Intern_JR101533", "workday", "workday:biotechne:JR101533"),
    ("https://jainglobal.wd5.myworkdayjobs.com/ExternalSite/job/London-Office/Quant-Research-Intern--Summer-2026---London-_JR100353-1", "workday", "workday:jainglobal:JR100353-1"),
    ("https://wd3.myworkdaysite.com/recruiting/acme/External/job/Remote/SWE-Intern_R123", "workday", "workday:acme:R123"),
    ("https://careers-calamp.icims.com/jobs/4326/intern-engineer/job?mobile=true&needsRedirect=false", "icims", "icims:careers-calamp:4326"),
    ("https://jobs.smartrecruiters.com/Visa/744000012345678-software-engineer-intern", "smartrecruiters", "smartrecruiters:744000012345678"),
    ("https://www.linkedin.com/jobs/view/4012345678/?trk=abc", "third_party", "url:https://www.linkedin.com/jobs/view/4012345678?trk=abc"),
    ("https://app.joinhandshake.com/jobs/9876543", "third_party", "url:https://app.joinhandshake.com/jobs/9876543"),
]


@pytest.mark.parametrize(("url", "ats", "key"), CASES)
def test_classify(url, ats, key):
    ref = classify(url)
    assert (ref.ats, ref.key) == (ats, key)


def test_lever_and_ashby_canonical_drop_apply_suffix():
    assert classify("https://jobs.lever.co/co/10746b3d-1760-4573-9b63-b93f5a5e4fc0/apply").canonical_url == (
        "https://jobs.lever.co/co/10746b3d-1760-4573-9b63-b93f5a5e4fc0"
    )
    assert classify("https://jobs.ashbyhq.com/dryft/3f1c261d-9b65-412b-9f17-34b8968bdd78/application").canonical_url == (
        "https://jobs.ashbyhq.com/dryft/3f1c261d-9b65-412b-9f17-34b8968bdd78"
    )


def test_canonicalize_strips_tracking_and_fragment():
    assert canonicalize("HTTPS://Careers.Example.com/jobs/42/?utm_source=Simplify&ref=Simplify&b=2&a=1#apply") == (
        "https://careers.example.com/jobs/42?a=1&b=2"
    )


def test_other_ats_keyed_by_canonical_url():
    ref = classify("https://www.tesla.com/careers/search/job/intern-software-engineer-123?utm_source=x")
    assert ref.ats == "other"
    assert ref.key == "url:https://www.tesla.com/careers/search/job/intern-software-engineer-123"
```

- [ ] **Step 4: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_ats.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'autoapply.ats'`).

- [ ] **Step 5: Implement** `src/autoapply/models.py`

```python
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
```

`src/autoapply/ats.py`:
```python
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
```

`src/autoapply/__main__.py`:
```python
from autoapply.cli import main

raise SystemExit(main())
```
(`cli.py` arrives in Task 8. `__main__` is not imported by tests.)

- [ ] **Step 6: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_ats.py -q`
Expected: all pass.

- [ ] **Step 7: Commit and create the private GitHub repo**

```bash
cd /c/Users/24GHi/Code/autoapply && git rev-parse --show-toplevel
git add pyproject.toml src tests && git diff --cached --name-only
git commit -m "feat: project scaffold, Posting model, ATS URL classification

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
gh repo create ghipszer20/autoapply --private --source . --remote origin --push
gh repo view ghipszer20/autoapply --json visibility -q .visibility
```
Expected: the last command prints `PRIVATE`. If it prints anything else, run `gh repo edit ghipszer20/autoapply --visibility private --accept-visibility-change-consequences` and report it.

---

### Task 3: SQLite store — upsert/merge, eligibility, state

**Files:**
- Create: `src/autoapply/db.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: `models.Posting`, `ats.classify`
- Produces:
  - `db.connect(path: str | Path) -> sqlite3.Connection` (row_factory=Row, schema created)
  - `db.UpsertStats(new: int, updated: int)`
  - `db.upsert_postings(conn, postings: Iterable[Posting], now: datetime) -> UpsertStats`
  - `db.iter_postings(conn) -> Iterator[tuple[str, Posting]]` (key, merged posting; `source` = comma-joined sources)
  - `db.set_eligibility(conn, results: Iterable[tuple[str, bool, str]]) -> None`
  - `db.get_state(conn, name: str, default: str | None = None) -> str | None`
  - `db.set_state(conn, name: str, value: str) -> None`
  - `db.is_enabled(conn) -> bool` (default False)
  - `db.counts(conn) -> dict` with keys `total`, `eligible`, `eligible_by_ats: dict[str,int]`, `top_rejects: list[tuple[str,int]]` (top 8)

- [ ] **Step 1: Write the failing test** `tests/test_db.py`

```python
from datetime import datetime, timedelta, timezone

from autoapply import db
from autoapply.models import Posting

T0 = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
GH = "https://job-boards.greenhouse.io/schonfeld/jobs/8180089"


def mk(url=GH, source="speedyapply-swe", **kw):
    base = dict(company="Schonfeld", title="2027 Software Engineering Intern", url=url, source=source)
    base.update(kw)
    return Posting(**base)


def test_insert_then_iter(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    stats = db.upsert_postings(conn, [mk(locations=("New York, NY",))], T0)
    assert (stats.new, stats.updated) == (1, 0)
    [(key, p)] = list(db.iter_postings(conn))
    assert key == "greenhouse:8180089"
    assert p.locations == ("New York, NY",)
    assert p.source == "speedyapply-swe"


def test_reupsert_keeps_first_seen(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    db.upsert_postings(conn, [mk()], T0)
    stats = db.upsert_postings(conn, [mk()], T0 + timedelta(hours=3))
    assert (stats.new, stats.updated) == (0, 1)
    row = conn.execute("SELECT first_seen, last_seen, COUNT(*) OVER () AS n FROM postings").fetchone()
    assert row["n"] == 1
    assert row["first_seen"] == T0.isoformat()
    assert row["last_seen"] == (T0 + timedelta(hours=3)).isoformat()


def test_vanished_posting_is_kept(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    db.upsert_postings(conn, [mk()], T0)
    db.upsert_postings(conn, [], T0 + timedelta(days=1))
    assert len(list(db.iter_postings(conn))) == 1


def test_cross_source_same_job_merges(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    db.upsert_postings(
        conn,
        [
            mk(source="speedyapply-swe"),
            mk(url="https://www.schonfeld.com/careers?gh_jid=8180089", source="simplify",
               terms=("Summer 2027",), degrees=("Bachelor's",)),
        ],
        T0,
    )
    [(key, p)] = list(db.iter_postings(conn))
    assert key == "greenhouse:8180089"
    assert p.source == "speedyapply-swe,simplify"
    assert p.terms == ("Summer 2027",)
    assert p.degrees == ("Bachelor's",)
    assert p.url == GH  # first-seen company-ATS URL kept


def test_company_url_replaces_third_party_url(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    li = "https://www.linkedin.com/jobs/view/1"
    db.upsert_postings(conn, [mk(url=li, source="alert")], T0)
    key = "url:" + li
    conn.execute("UPDATE postings SET key=? WHERE key=?", ("greenhouse:8180089", key))
    db.upsert_postings(conn, [mk(source="simplify")], T0)
    row = conn.execute("SELECT ats, url FROM postings").fetchone()
    assert (row["ats"], row["url"]) == ("greenhouse", GH)


def test_eligibility_and_counts(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    db.upsert_postings(conn, [mk(), mk(url="https://jobs.lever.co/x/10746b3d-1760-4573-9b63-b93f5a5e4fc0")], T0)
    db.set_eligibility(conn, [("greenhouse:8180089", True, ""),
                              ("lever:10746b3d-1760-4573-9b63-b93f5a5e4fc0", False, "location")])
    c = db.counts(conn)
    assert c["total"] == 2 and c["eligible"] == 1
    assert c["eligible_by_ats"] == {"greenhouse": 1}
    assert c["top_rejects"] == [("location", 1)]


def test_state_defaults_disabled(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    assert db.is_enabled(conn) is False
    db.set_state(conn, "enabled", "1")
    assert db.is_enabled(conn) is True
    assert db.get_state(conn, "missing", "x") == "x"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_db.py -q`
Expected: FAIL (`cannot import name 'db'`).

- [ ] **Step 3: Implement** `src/autoapply/db.py`

```python
"""SQLite store: one row per unique job (keyed by ats.classify), plus key/value state."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
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
"""


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
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_db.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd /c/Users/24GHi/Code/autoapply && git rev-parse --show-toplevel
git add src/autoapply/db.py tests/test_db.py && git diff --cached --name-only
git commit -m "feat: SQLite posting store with cross-source merge and state

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: speedyapply source (SWE + AI lists)

**Files:**
- Create: `src/autoapply/sources/__init__.py` (empty docstring), `src/autoapply/sources/base.py`, `src/autoapply/sources/speedyapply.py`, `tests/fixtures/speedyapply_sample.md`, `tests/test_speedyapply.py`

**Interfaces:**
- Consumes: `models.Posting`
- Produces:
  - `sources.base.SourceError(Exception)`
  - `sources.base.FetchFn = Callable[[httpx.Client, datetime], list[Posting]]`
  - `speedyapply.LISTS: dict[str, tuple[str, str]]` (source name → (url, default category))
  - `speedyapply.parse(markdown: str, source: str, default_category: str, now: datetime) -> list[Posting]`
  - `speedyapply.fetch(client: httpx.Client, now: datetime, *, source: str) -> list[Posting]`

Format facts (verified 2026-09-29): both READMEs are USA internships only. `### FAANG+`, `### Quant` and `### Other` headings each precede a table. FAANG+ and Quant have columns `Company | Position | Location | Salary | Posting | Age`, and Other drops `Salary`. Company is `<a href=...><strong>Name</strong></a>`, Posting is `<a href="URL"><img ...></a>`, and Age is `Nd`.

- [ ] **Step 1: Create fixture** `tests/fixtures/speedyapply_sample.md`

```markdown
# 2027 Software Engineering Internship & New Grad Positions

### USA Positions :eagle:
- [Internships :books:](/) - **3** available

## 2027 USA SWE Internships :books::eagle:

### FAANG+

| Company | Position | Location | Salary | Posting | Age |
|---|---|---|---|---|---|
| <a href="https://www.microsoft.com"><strong>Microsoft</strong></a> | Software Engineer: Intern Opportunities - Atlanta | Atlanta, GA | $52/hr | <a href="https://apply.careers.microsoft.com/careers/job/1970393557008714"><img src="https://i.imgur.com/JpkfjIq.png" alt="Apply" width="70"/></a> | 2d |

### Quant

| Company | Position | Location | Salary | Posting | Age |
|---|---|---|---|---|---|
| <a href="https://www.schonfeld.com"><strong>Schonfeld</strong></a> | 2027 Software Engineering Intern | New York City, NY | $106/hr | <a href="https://job-boards.greenhouse.io/schonfeld/jobs/8180089"><img src="https://i.imgur.com/JpkfjIq.png" alt="Apply" width="70"/></a> | 24d |

### Other

| Company | Position | Location | Posting | Age |
|---|---|---|---|---|
| <a href="https://www.att.com"><strong>AT&amp;T</strong></a> | Software Engineering Co-op - Summer/Fall 2027 | Dallas, TX +3 | <a href="https://globalhr.wd5.myworkdayjobs.com/en-US/ext/job/Dallas/SWE-Co-op_01870236"><img src="https://i.imgur.com/JpkfjIq.png" alt="Apply" width="70"/></a> | 1d |
```

- [ ] **Step 2: Write the failing test** `tests/test_speedyapply.py`

```python
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from autoapply.sources import speedyapply
from autoapply.sources.base import SourceError

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def load(fixtures):
    return (fixtures / "speedyapply_sample.md").read_text(encoding="utf-8")


def test_parses_all_sections(fixtures):
    ps = speedyapply.parse(load(fixtures), "speedyapply-swe", "Software", NOW)
    assert [p.company for p in ps] == ["Microsoft", "Schonfeld", "AT&T"]
    ms, sch, att = ps
    assert ms.category == "Software" and sch.category == "Quant" and att.category == "Software"
    assert sch.url == "https://job-boards.greenhouse.io/schonfeld/jobs/8180089"
    assert sch.title == "2027 Software Engineering Intern"
    assert sch.posted_at == NOW - timedelta(days=24)
    assert all(p.source == "speedyapply-swe" for p in ps)


def test_entities_and_location_suffix(fixtures):
    att = speedyapply.parse(load(fixtures), "speedyapply-swe", "Software", NOW)[2]
    assert att.company == "AT&T"
    assert att.locations == ("Dallas, TX", "USA")  # list is USA-only; marker lets the US filter pass


def test_no_table_raises():
    with pytest.raises(SourceError):
        speedyapply.parse("# nothing here\n", "speedyapply-swe", "Software", NOW)


def test_header_without_rows_raises():
    md = "### Other\n\n| Company | Position | Location | Posting | Age |\n|---|---|---|---|---|\n"
    with pytest.raises(SourceError):
        speedyapply.parse(md, "speedyapply-swe", "Software", NOW)


def test_fetch_uses_list_url(fixtures):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=load(fixtures))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ps = speedyapply.fetch(client, NOW, source="speedyapply-ai")
    assert seen == [speedyapply.LISTS["speedyapply-ai"][0]]
    assert ps[0].category == "AI/ML/Data"
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_speedyapply.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 4: Implement**

`src/autoapply/sources/__init__.py`:
```python
"""Posting sources. Each exposes fetch(client, now, ...) -> list[Posting]."""
```

`src/autoapply/sources/base.py`:
```python
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import httpx

from ..models import Posting


class SourceError(Exception):
    """A source returned data we could not parse; never silently yield zero postings."""


FetchFn = Callable[[httpx.Client, datetime], list[Posting]]
```

`src/autoapply/sources/speedyapply.py`:
```python
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
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_speedyapply.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /c/Users/24GHi/Code/autoapply && git rev-parse --show-toplevel
git add src/autoapply/sources tests/fixtures/speedyapply_sample.md tests/test_speedyapply.py && git diff --cached --name-only
git commit -m "feat: speedyapply SWE and AI list source

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: SimplifyJobs source

**Files:**
- Create: `src/autoapply/sources/simplify.py`, `tests/fixtures/simplify_sample.json`, `tests/test_simplify.py`

**Interfaces:**
- Consumes: `models.Posting`, `sources.base.SourceError`
- Produces: `simplify.URL: str`, `simplify.parse(items: object) -> list[Posting]`, `simplify.fetch(client, now) -> list[Posting]`

Format facts (verified 2026-09-29): `listings.json` is a list of dicts with keys `active, category, company_name, company_url, date_posted (epoch s), date_updated, degrees, id, is_visible, locations, source, sponsorship, terms, title, url`. It already includes off-season terms (Fall/Winter/Spring), so no second repo is needed. This supersedes the spec's "plus the off-season repo" line.

- [ ] **Step 1: Create fixture** `tests/fixtures/simplify_sample.json`

```json
[
  {"active": true, "is_visible": true, "category": "Software", "company_name": "Cresta",
   "date_posted": 1790000000, "degrees": ["Bachelor's"], "id": "a1", "locations": ["San Francisco, CA"],
   "sponsorship": "Other", "terms": ["Summer 2027"], "title": "Software Engineer Intern",
   "url": "https://job-boards.greenhouse.io/cresta/jobs/5106468008?utm_source=Simplify&ref=Simplify"},
  {"active": false, "is_visible": true, "category": "Software", "company_name": "Gone Inc",
   "date_posted": 1780000000, "degrees": [], "id": "a2", "locations": ["Austin, TX"],
   "sponsorship": "Other", "terms": ["Summer 2027"], "title": "SWE Intern", "url": "https://gone.example/jobs/1"},
  {"active": true, "is_visible": false, "category": "Quant", "company_name": "Hidden LLC",
   "date_posted": 1790000000, "degrees": [], "id": "a3", "locations": [],
   "sponsorship": "Other", "terms": ["Summer 2027"], "title": "Quant Dev Intern", "url": "https://hidden.example/1"},
  {"active": true, "is_visible": true, "category": "Quant", "company_name": "Procter &amp; Gamble",
   "date_posted": 1790000000, "degrees": ["Bachelor's", "Master's"], "id": "a4", "locations": ["Remote in USA"],
   "sponsorship": "U.S. Citizenship is Required", "terms": ["Spring 2027", "Summer 2027"],
   "title": "Data Science Co-op", "url": "https://pg.wd5.myworkdayjobs.com/en-US/pg/job/Remote/DS-Coop_R999"}
]
```

- [ ] **Step 2: Write the failing test** `tests/test_simplify.py`

```python
import json
from datetime import datetime, timezone

import httpx
import pytest

from autoapply.sources import simplify
from autoapply.sources.base import SourceError

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def items(fixtures):
    return json.loads((fixtures / "simplify_sample.json").read_text(encoding="utf-8"))


def test_skips_inactive_and_hidden(fixtures):
    ps = simplify.parse(items(fixtures))
    assert [p.company for p in ps] == ["Cresta", "Procter & Gamble"]


def test_fields(fixtures):
    cresta, pg = simplify.parse(items(fixtures))
    assert cresta.terms == ("Summer 2027",) and cresta.degrees == ("Bachelor's",)
    assert cresta.posted_at == datetime.fromtimestamp(1790000000, tz=timezone.utc)
    assert cresta.source == "simplify" and cresta.category == "Software"
    assert pg.sponsorship == "U.S. Citizenship is Required"
    assert pg.locations == ("Remote in USA",)


def test_bad_shape_raises():
    with pytest.raises(SourceError):
        simplify.parse({"not": "a list"})
    with pytest.raises(SourceError):
        simplify.parse([])


def test_fetch(fixtures):
    def handler(request):
        assert str(request.url) == simplify.URL
        return httpx.Response(200, json=items(fixtures))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert len(simplify.fetch(client, NOW)) == 2
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_simplify.py -q`
Expected: FAIL (`ImportError`).

- [ ] **Step 4: Implement** `src/autoapply/sources/simplify.py`

```python
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
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_simplify.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /c/Users/24GHi/Code/autoapply && git rev-parse --show-toplevel
git add src/autoapply/sources/simplify.py tests/fixtures/simplify_sample.json tests/test_simplify.py && git diff --cached --name-only
git commit -m "feat: SimplifyJobs listings source

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Company job boards (Greenhouse / Lever / Ashby public APIs)

**Files:**
- Create: `src/autoapply/sources/boards.py`, `tests/test_boards.py`

**Interfaces:**
- Consumes: `models.Posting`, `sources.base.SourceError`
- Produces:
  - `boards.INTERN_RE: re.Pattern`
  - `boards.fetch_greenhouse(client, now, *, token: str, name: str) -> list[Posting]`
  - `boards.fetch_lever(client, now, *, token: str, name: str) -> list[Posting]`
  - `boards.fetch_ashby(client, now, *, token: str, name: str) -> list[Posting]`
  - The source name for each is `f"board:{ats}:{token}"`.

Board APIs return every job, including full-time roles, so these sources keep only internship-like titles. Response shapes (verified 2026-09-29):
- Greenhouse: `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs` returns `{"jobs":[{"id","title","absolute_url","location":{"name"},"first_published","company_name",...}]}`.
- Lever: `GET https://api.lever.co/v0/postings/{token}?mode=json` returns `[{"id","text","hostedUrl","categories":{"location","allLocations"},"createdAt"(ms)}]`, or an error object `{...}` for an unknown token.
- Ashby: `GET https://api.ashbyhq.com/posting-api/job-board/{token}` returns `{"jobs":[{"id","title","jobUrl","location","secondaryLocations":[{"location"}],"publishedAt","isListed"}]}`.

- [ ] **Step 1: Write the failing test** `tests/test_boards.py`

```python
from datetime import datetime, timezone

import httpx
import pytest

from autoapply.sources import boards
from autoapply.sources.base import SourceError

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def client_for(payload, status=200):
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=payload)))


def test_greenhouse_keeps_interns_only():
    payload = {"jobs": [
        {"id": 1, "title": "Software Engineer Intern (Summer 2027)", "absolute_url": "https://job-boards.greenhouse.io/imc/jobs/1",
         "location": {"name": "Chicago, IL"}, "first_published": "2026-09-17T19:59:44-04:00", "company_name": "IMC"},
        {"id": 2, "title": "Senior Network Engineer", "absolute_url": "https://job-boards.greenhouse.io/imc/jobs/2",
         "location": {"name": "Chicago, IL"}, "first_published": None, "company_name": "IMC"},
    ]}
    with client_for(payload) as c:
        [p] = boards.fetch_greenhouse(c, NOW, token="imc", name="IMC")
    assert p.title.startswith("Software Engineer Intern")
    assert p.source == "board:greenhouse:imc" and p.company == "IMC"
    assert p.locations == ("Chicago, IL",)
    assert p.posted_at == datetime.fromisoformat("2026-09-17T19:59:44-04:00")


def test_lever():
    payload = [
        {"id": "u1", "text": "Software Engineer Intern - Summer 2027",
         "hostedUrl": "https://jobs.lever.co/belvederetrading/10746b3d-1760-4573-9b63-b93f5a5e4fc0",
         "categories": {"location": "Chicago, Illinois", "allLocations": ["Chicago, Illinois", "Boulder, CO"]},
         "createdAt": 1790110014068},
        {"id": "u2", "text": "Early Career Talent Partner", "hostedUrl": "https://jobs.lever.co/belvederetrading/x",
         "categories": {}, "createdAt": 1790110014068},
    ]
    with client_for(payload) as c:
        [p] = boards.fetch_lever(c, NOW, token="belvederetrading", name="Belvedere Trading")
    assert p.company == "Belvedere Trading"
    assert p.locations == ("Chicago, Illinois", "Boulder, CO")
    assert p.posted_at == datetime.fromtimestamp(1790110014.068, tz=timezone.utc)


def test_lever_unknown_token_raises():
    with client_for({"ok": False, "error": "Document not found"}) as c, pytest.raises(SourceError):
        boards.fetch_lever(c, NOW, token="nope", name="Nope")


def test_ashby_skips_unlisted():
    payload = {"jobs": [
        {"id": "a", "title": "Machine Learning Intern", "jobUrl": "https://jobs.ashbyhq.com/ramp/b66be397-240b-41a6-9b05-493299b270a9",
         "location": "New York, NY", "secondaryLocations": [{"location": "San Francisco, CA"}],
         "publishedAt": "2026-09-24T13:51:56.459+00:00", "isListed": True},
        {"id": "b", "title": "Data Intern", "jobUrl": "https://jobs.ashbyhq.com/ramp/x", "location": "NY",
         "secondaryLocations": [], "publishedAt": None, "isListed": False},
    ]}
    with client_for(payload) as c:
        [p] = boards.fetch_ashby(c, NOW, token="ramp", name="Ramp")
    assert p.locations == ("New York, NY", "San Francisco, CA")


@pytest.mark.parametrize("title", ["SWE Intern", "Software Engineering Internship", "Data Science Co-op",
                                   "Quant Dev Coop", "Summer 2027 Software Engineer"])
def test_intern_re_matches(title):
    assert boards.INTERN_RE.search(title)


@pytest.mark.parametrize("title", ["Internal Tools Engineer", "International Sales", "Software Engineer"])
def test_intern_re_rejects(title):
    assert not boards.INTERN_RE.search(title)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_boards.py -q`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement** `src/autoapply/sources/boards.py`

```python
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
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_boards.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd /c/Users/24GHi/Code/autoapply && git rev-parse --show-toplevel
git add src/autoapply/sources/boards.py tests/test_boards.py && git diff --cached --name-only
git commit -m "feat: Greenhouse/Lever/Ashby company board sources

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Config and eligibility filter

**Files:**
- Create: `config.yaml`, `src/autoapply/config.py`, `src/autoapply/filter.py`, `tests/test_config.py`, `tests/test_filter.py`

**Interfaces:**
- Consumes: `models.Posting`
- Produces:
  - `config.FilterConfig` (pydantic: `categories: list[str]`, `allowed_terms: list[str]`, `title_include: list[str]`, `title_exclude: list[str]`, `require_bachelors: bool`, `us_only: bool`, `us_citizen: bool`)
  - `config.Config` (`filter: FilterConfig`, `boards: dict[str, dict[str, str]]` = ats → {token: display name})
  - `config.load_config(path: str | Path) -> Config` (raises `ConfigError` with a readable message)
  - `filter.evaluate(p: Posting, cfg: FilterConfig) -> tuple[bool, str]`
  - `filter.is_us_location(loc: str) -> bool`

Scope note: the user asked for **quant developer/SWE**. `title_include` therefore has no bare `quant` pattern: quant dev/SWE titles match through `software|developer|engineer`, and quant *research/trading* titles don't match. Enabling those is a one-line config change.

- [ ] **Step 1: Ask the user** whether they are a U.S. citizen (this decides whether "U.S. Citizenship is Required" postings are eligible). Write their answer into `config.yaml` below in place of `REPLACE_ME`. Don't guess.

- [ ] **Step 2: Create** `config.yaml`

```yaml
# autoapply configuration (committed; no secrets here)
filter:
  categories: ["Software", "AI/ML/Data", "Quant"]      # SimplifyJobs/speedyapply category names
  allowed_terms: ["Summer 2027", "Fall 2027", "Spring 2027", "Winter 2027"]
  title_include:                                          # at least one must match (case-insensitive)
    - 'software|\bswe\b|developer|engineer|programm'
    - 'data|machine learning|\bml\b|\bai\b|artificial intelligence|analytics'
  title_exclude:                                          # any match rejects
    - '\bph\.?d\b|\bmasters?\b|master''s|\bmba\b'
    - 'hardware|mechanical|electrical|civil|chemical|manufacturing|\basic\b|\bfpga\b|\brf\b'
    - 'product manag|program manag|\bsales\b|marketing|recruit|designer|\bux\b'
    - '\btrader\b|trading intern'
    - 'clearance|ts/sci'
  require_bachelors: true
  us_only: true
  us_citizen: REPLACE_ME   # true or false; loading fails until set

boards:                    # verified 2026-09-29; token: display name
  greenhouse:
    wehrtyou: Hudson River Trading
    jumptrading: Jump Trading
    imc: IMC
    optiverus: Optiver
    drweng: DRW
    schonfeld: Schonfeld
    virtu: Virtu Financial
    towerresearchcapital: Tower Research Capital
    akunacapital: Akuna Capital
    point72: Point72
    clearstreet: Clear Street
    flowtraders: Flow Traders
  lever:
    belvederetrading: Belvedere Trading
  ashby: {}
```
(Jane Street's Greenhouse board lists no internship-titled roles, so Jane Street comes from the listing repos instead.)

- [ ] **Step 3: Write failing tests**

`tests/test_config.py`:
```python
from pathlib import Path

import pytest

from autoapply.config import ConfigError, load_config

ROOT = Path(__file__).parent.parent


def test_repo_config_loads():
    cfg = load_config(ROOT / "config.yaml")
    assert "Software" in cfg.filter.categories
    assert cfg.boards["greenhouse"]["wehrtyou"] == "Hudson River Trading"


def test_unset_citizenship_is_readable_error(tmp_path):
    text = (ROOT / "config.yaml").read_text(encoding="utf-8")
    import re
    bad = re.sub(r"us_citizen: \S+", "us_citizen: REPLACE_ME", text)
    (tmp_path / "c.yaml").write_text(bad, encoding="utf-8")
    with pytest.raises(ConfigError, match="us_citizen"):
        load_config(tmp_path / "c.yaml")


def test_bad_regex_is_readable_error(tmp_path):
    (tmp_path / "c.yaml").write_text(
        "filter:\n  categories: []\n  allowed_terms: []\n  title_include: ['(']\n  title_exclude: []\n"
        "  require_bachelors: true\n  us_only: true\n  us_citizen: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="title_include"):
        load_config(tmp_path / "c.yaml")
```

`tests/test_filter.py`:
```python
import pytest

from autoapply.config import FilterConfig
from autoapply.filter import evaluate, is_us_location
from autoapply.models import Posting

CFG = FilterConfig(
    categories=["Software", "AI/ML/Data", "Quant"],
    allowed_terms=["Summer 2027", "Fall 2027", "Spring 2027", "Winter 2027"],
    title_include=[r"software|\bswe\b|developer|engineer|programm",
                   r"data|machine learning|\bml\b|\bai\b|artificial intelligence|analytics"],
    title_exclude=[r"\bph\.?d\b|\bmasters?\b|master's|\bmba\b", r"hardware|mechanical", r"\btrader\b"],
    require_bachelors=True, us_only=True, us_citizen=False,
)


def P(**kw):
    base = dict(company="Acme", title="Software Engineer Intern", url="https://x/1", source="simplify",
                locations=("Austin, TX",), terms=("Summer 2027",), category="Software", degrees=("Bachelor's",))
    base.update(kw)
    return Posting(**base)


def test_happy_path():
    assert evaluate(P(), CFG) == (True, "")


@pytest.mark.parametrize(("kw", "reason"), [
    (dict(category="Hardware"), "category:Hardware"),
    (dict(title="Machine Learning Intern - PhD"), "title_exclude"),
    (dict(title="Quantitative Trader Intern"), "title_exclude"),
    (dict(title="Finance Intern"), "title_include"),
    (dict(terms=("Summer 2026",)), "terms"),
    (dict(terms=(), title="Software Engineer Intern - Fall 2026"), "terms"),
    (dict(degrees=("Master's", "PhD")), "degree"),
    (dict(locations=("London, UK",)), "location"),
    (dict(locations=("Toronto, ON",)), "location"),
    (dict(sponsorship="U.S. Citizenship is Required"), "citizenship"),
])
def test_rejections(kw, reason):
    ok, why = evaluate(P(**kw), CFG)
    assert not ok and why.startswith(reason)


def test_quant_dev_passes_via_engineer_pattern():
    assert evaluate(P(title="Quantitative Developer Intern", category="Quant"), CFG)[0]


def test_empty_category_and_terms_rely_on_title():
    assert evaluate(P(category="", terms=(), title="SWE Intern - Summer 2027"), CFG) == (True, "")


def test_citizen_allows_citizenship_roles():
    cfg = CFG.model_copy(update={"us_citizen": True})
    assert evaluate(P(sponsorship="U.S. Citizenship is Required"), cfg)[0]


@pytest.mark.parametrize(("loc", "us"), [
    ("Austin, TX", True), ("Washington, DC", True), ("Remote in USA", True), ("Remote", True),
    ("USA", True), ("United States", True), ("Anywhere in the U.S.", True), ("London, UK", False), ("Toronto, ON", False),
    ("Remote in Canada", False), ("APAC", False),
])
def test_is_us_location(loc, us):
    assert is_us_location(loc) is us
```

- [ ] **Step 4: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_config.py tests/test_filter.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 5: Implement**

`src/autoapply/config.py`:
```python
from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError, field_validator


class ConfigError(Exception):
    pass


class FilterConfig(BaseModel):
    categories: list[str]
    allowed_terms: list[str]
    title_include: list[str]
    title_exclude: list[str]
    require_bachelors: bool = True
    us_only: bool = True
    us_citizen: bool

    @field_validator("title_include", "title_exclude")
    @classmethod
    def _valid_regexes(cls, patterns: list[str]) -> list[str]:
        for p in patterns:
            try:
                re.compile(p)
            except re.error as e:
                raise ValueError(f"bad regex {p!r}: {e}") from e
        return patterns


class Config(BaseModel):
    filter: FilterConfig
    boards: dict[str, dict[str, str]] = {}


def load_config(path: str | Path) -> Config:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return Config.model_validate(raw)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
        raise ConfigError(f"{path}: {problems}") from e
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"{path}: {e}") from e
```

`src/autoapply/filter.py`:
```python
"""Rule-based eligibility. Returns (eligible, reason); reason is '' when eligible."""

from __future__ import annotations

import re

from .config import FilterConfig
from .models import Posting

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA",
    "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC", "PR",
}
_STATE_SUFFIX = re.compile(r",\s*([A-Z]{2})\b")
_US_WORDS = re.compile(r"\b(?:USA|United States)\b|\bU\.S\.")


def is_us_location(loc: str) -> bool:
    if _US_WORDS.search(loc):
        return True
    if (m := _STATE_SUFFIX.search(loc)) and m.group(1) in US_STATES:
        return True
    return loc.strip().lower() == "remote"


def evaluate(p: Posting, cfg: FilterConfig) -> tuple[bool, str]:
    if p.category and p.category not in cfg.categories:
        return False, f"category:{p.category}"
    for pat in cfg.title_exclude:
        if re.search(pat, p.title, re.IGNORECASE):
            return False, f"title_exclude:{pat}"
    if not any(re.search(pat, p.title, re.IGNORECASE) for pat in cfg.title_include):
        return False, "title_include:none"
    if p.terms:
        if not any(t in cfg.allowed_terms for t in p.terms):
            return False, f"terms:{','.join(p.terms)}"
    elif re.search(r"\b2026\b", p.title) and "2027" not in p.title:
        return False, "terms:title-2026"
    if cfg.require_bachelors and p.degrees and "Bachelor's" not in p.degrees:
        return False, "degree"
    if cfg.us_only and p.locations and not any(is_us_location(loc) for loc in p.locations):
        return False, "location"
    if not cfg.us_citizen and p.sponsorship == "U.S. Citizenship is Required":
        return False, "citizenship"
    return True, ""
```

- [ ] **Step 6: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_config.py tests/test_filter.py -q`
Expected: all pass. (`test_repo_config_loads` needs Step 1's real `true`/`false` in `config.yaml`.)

- [ ] **Step 7: Commit**

```bash
cd /c/Users/24GHi/Code/autoapply && git rev-parse --show-toplevel
git add config.yaml src/autoapply/config.py src/autoapply/filter.py tests/test_config.py tests/test_filter.py && git diff --cached --name-only
git commit -m "feat: YAML config and rule-based eligibility filter

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Source registry and CLI (`discover`, `on`, `off`, `status`)

**Files:**
- Create: `src/autoapply/sources/registry.py`, `src/autoapply/cli.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `registry.build_sources(cfg: Config) -> list[tuple[str, FetchFn]]`
  - `cli.discover(conn, cfg, client, now, out: TextIO) -> int`
  - `cli.main(argv: list[str] | None = None) -> int`
  - CLI flags: `--db PATH` (default `autoapply.db` in the current directory), `--config PATH` (default `config.yaml`)

- [ ] **Step 1: Write the failing test** `tests/test_cli.py`

```python
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from autoapply import cli, db
from autoapply.config import Config, FilterConfig
from autoapply.sources import simplify, speedyapply
from autoapply.sources.registry import build_sources

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)
FIX = Path(__file__).parent / "fixtures"
CFG = Config(
    filter=FilterConfig(categories=["Software", "AI/ML/Data", "Quant"], allowed_terms=["Summer 2027", "Spring 2027"],
                        title_include=[r"software|engineer|data"], title_exclude=[r"\bphd\b"],
                        require_bachelors=True, us_only=True, us_citizen=False),
    boards={"greenhouse": {"imc": "IMC"}},
)


def handler_factory(fail: set[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if any(f in url for f in fail):
            return httpx.Response(500)
        if url == simplify.URL:
            return httpx.Response(200, json=json.loads((FIX / "simplify_sample.json").read_text(encoding="utf-8")))
        if "speedyapply" in url:
            return httpx.Response(200, text=(FIX / "speedyapply_sample.md").read_text(encoding="utf-8"))
        if "greenhouse.io/v1/boards/imc" in url:
            return httpx.Response(200, json={"jobs": []})
        return httpx.Response(404)
    return handler


def run(tmp_path, fail=frozenset()):
    conn = db.connect(tmp_path / "t.db")
    out = io.StringIO()
    with httpx.Client(transport=httpx.MockTransport(handler_factory(set(fail)))) as client:
        code = cli.discover(conn, CFG, client, NOW, out)
    return code, out.getvalue(), conn


def test_build_sources_names():
    names = [n for n, _ in build_sources(CFG)]
    assert names == ["speedyapply-swe", "speedyapply-ai", "simplify", "board:greenhouse:imc"]


def test_discover_stores_dedupes_and_filters(tmp_path):
    code, out, conn = run(tmp_path)
    assert code == 0
    c = db.counts(conn)
    # speedyapply fixture (3 rows) served for both SWE and AI lists -> same keys merge; + 2 simplify
    assert c["total"] == 5
    assert c["eligible"] >= 1
    assert "eligible" in out and "simplify: 2 postings" in out


def test_discover_survives_failing_source(tmp_path):
    code, out, conn = run(tmp_path, fail={"listings.json"})
    assert code == 0
    assert "! simplify" in out
    assert db.counts(conn)["total"] == 3


def test_discover_all_fail_exit_1(tmp_path):
    code, out, _ = run(tmp_path, fail={"speedyapply", "listings.json", "greenhouse"})
    assert code == 1


def test_discover_records_timestamp(tmp_path):
    _, _, conn = run(tmp_path)
    assert db.get_state(conn, "last_discover") == NOW.isoformat()


def test_on_off_status(tmp_path, capsys):
    dbp = str(tmp_path / "s.db")
    assert cli.main(["--db", dbp, "status"]) == 0
    assert "enabled: no" in capsys.readouterr().out
    cli.main(["--db", dbp, "on"])
    cli.main(["--db", dbp, "status"])
    assert "enabled: yes" in capsys.readouterr().out
    cli.main(["--db", dbp, "off"])
    cli.main(["--db", dbp, "status"])
    assert "enabled: no" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -q`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement**

`src/autoapply/sources/registry.py`:
```python
from __future__ import annotations

from functools import partial

from ..config import Config
from . import boards, simplify, speedyapply
from .base import FetchFn

_BOARD_FETCHERS = {"greenhouse": boards.fetch_greenhouse, "lever": boards.fetch_lever, "ashby": boards.fetch_ashby}


def build_sources(cfg: Config) -> list[tuple[str, FetchFn]]:
    out: list[tuple[str, FetchFn]] = [(name, partial(speedyapply.fetch, source=name)) for name in speedyapply.LISTS]
    out.append(("simplify", simplify.fetch))
    for ats, entries in cfg.boards.items():
        fetch = _BOARD_FETCHERS[ats]
        out.extend((f"board:{ats}:{tok}", partial(fetch, token=tok, name=name)) for tok, name in entries.items())
    return out
```

`src/autoapply/cli.py`:
```python
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
```

- [ ] **Step 4: Run the whole suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Live smoke run** (real network; writes the gitignored `autoapply.db`)

```bash
cd /c/Users/24GHi/Code/autoapply && .venv/Scripts/autoapply discover && .venv/Scripts/autoapply status
```
Expected: every source prints a count, or a `!` line with a reason. The totals should be in the thousands, and eligible should be in the hundreds or more. `enabled: no`. Also check for sources returning 0 postings. For a listing repo that's a format change; for a board it can be legitimate (no open internships). Report the printed summary to the user.

- [ ] **Step 6: Commit**

```bash
cd /c/Users/24GHi/Code/autoapply && git rev-parse --show-toplevel
git add src/autoapply/sources/registry.py src/autoapply/cli.py tests/test_cli.py && git diff --cached --name-only
git commit -m "feat: autoapply discover/on/off/status CLI

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push
```

---

### Task 9: Phone control via Remote Control (CLAUDE.md, README, user-run spike)

**Files:**
- Create: `CLAUDE.md`, `README.md`
- Modify: `docs/spikes/2026-09-29-spikes.md` (section B)

- [ ] **Step 1: Create** `CLAUDE.md`

```markdown
# autoapply — operating instructions for Claude Code sessions

This repo runs an automatic internship-application pipeline. The user usually talks to this
session from the Claude mobile app through Remote Control. Keep replies to one or two short lines.

## Phone commands
Run commands from the repo root with `.venv/Scripts/autoapply`:
- "on" / "start" / "resume": run `.venv/Scripts/autoapply on`, then `.venv/Scripts/autoapply status`
- "off" / "stop" / "pause": run `.venv/Scripts/autoapply off`, then `.venv/Scripts/autoapply status`
- "status" / "how's it going": run `.venv/Scripts/autoapply status` and summarize in one line

Never edit code, config, or git state in response to a phone command. If a request is anything other
than on/off/status, say so and ask the user to do it from the PC.

## Development rules
- Own git repo at this folder; the parent home dir is a separate repo with secrets. Check
  `git rev-parse --show-toplevel` before staging and review `git diff --cached --name-only`.
- Private GitHub repo; no GitHub Actions.
- Tests: `.venv/Scripts/python -m pytest -q` (network-free); live checks: `-m live`.
```

- [ ] **Step 2: Create** `README.md`

```markdown
# autoapply

Personal pipeline that finds Summer 2027 / off-cycle internships (SWE, data science, quant dev, AI/ML)
and applies automatically on each company's own application system.

Design: `docs/superpowers/specs/2026-09-29-autoapply-design.md`

## Setup
    py -3.13 -m venv .venv
    .venv/Scripts/python -m pip install -e ".[dev]"
    # set filter.us_citizen in config.yaml

## Use
    .venv/Scripts/autoapply discover   # fetch + dedupe + filter into autoapply.db
    .venv/Scripts/autoapply status
    .venv/Scripts/autoapply on|off     # kill switch (new installs start off)

## Phone control
Start `claude --remote-control` in this folder, then enable push notifications in `/config`.
From the Claude app, say "on", "off" or "status". See CLAUDE.md.
```

- [ ] **Step 3: Commit and push**

```bash
cd /c/Users/24GHi/Code/autoapply && git rev-parse --show-toplevel
git add CLAUDE.md README.md && git diff --cached --name-only
git commit -m "docs: phone-control instructions and README

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push
```

- [ ] **Step 4: User-run spike B** (needs the user's phone; walk them through it)
  1. In a new terminal: `cd C:\Users\24GHi\Code\autoapply` then `claude --remote-control`.
  2. In that session run `/config` and turn on push notifications.
  3. On the phone, open the Claude app, find the session, and send "status", then "on", then "off". Approve the command permission prompts (choose "don't ask again" for `autoapply`).
  4. On the PC: `.venv/Scripts/autoapply status` should show `enabled: no`.

Record in `docs/spikes/2026-09-29-spikes.md` section B: whether the session was visible on the phone, whether commands ran, whether permission prompts appeared on the phone, and whether a push notification arrived. Commit:

```bash
git add docs/spikes/2026-09-29-spikes.md && git commit -m "docs: record Remote Control phone spike

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>" && git push
```
