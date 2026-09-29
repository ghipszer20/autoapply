from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoapply import db
from autoapply.config import Config, FilterConfig
from autoapply.runner import Locked, RunLock, run_cycle
from autoapply.schedule import install_script

CFG = Config(filter=FilterConfig(categories=[], allowed_terms=[], title_include=[], title_exclude=[], us_citizen=True))
NOW = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)


def test_live_run_respects_kill_switch(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    rep = run_cycle(CFG, conn, live=True, now=lambda: NOW)
    assert rep.status == "disabled" and rep.outcomes == []


def test_daily_cap(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.set_state(conn, "enabled", "1")
    cfg = CFG.model_copy(update={"run": CFG.run.model_copy(update={"daily_cap": 0})})
    assert run_cycle(cfg, conn, live=True, now=lambda: NOW).status == "cap"


def test_no_candidates(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    rep = run_cycle(CFG, conn, live=False, now=lambda: NOW)
    assert "no eligible postings" in rep.lines[0]


def test_discover_when_stale(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    calls = []
    run_cycle(CFG, conn, live=False, now=lambda: NOW, discover_fn=lambda: calls.append(1))
    db.set_state(conn, "last_discover", NOW.isoformat())
    run_cycle(CFG, conn, live=False, now=lambda: NOW, discover_fn=lambda: calls.append(1))
    assert calls == [1]


def test_run_lock(tmp_path):
    lock = tmp_path / "run.lock"
    with RunLock(lock):
        with pytest.raises(Locked):
            with RunLock(lock):
                pass
    assert not lock.exists()


def test_schedule_uses_pythonw():
    s = install_script(Path(r"C:\x\autoapply"))
    assert r"C:\x\autoapply\.venv\Scripts\pythonw.exe" in s and "-m autoapply run" in s
    assert "cmd.exe" not in s and "-WakeToRun" in s and "IgnoreNew" in s
