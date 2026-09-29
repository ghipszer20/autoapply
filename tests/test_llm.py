import json
from datetime import date

import pytest
from pydantic import BaseModel

from autoapply import db
from autoapply.llm import LLM, BudgetExceeded, LLMError


class Verdict(BaseModel):
    ok: bool
    why: str


def envelope(structured=None, result="", is_error=False):
    return json.dumps({"type": "result", "is_error": is_error, "result": result, "structured_output": structured})


class FakeRunner:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls: list[list[str]] = []

    def __call__(self, args, timeout):
        self.calls.append(args)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "t.db")


def mk(conn, runner, **kw):
    return LLM(conn=conn, runner=runner, claude_path="claude", today=lambda: date(2026, 9, 29), **kw)


def test_structured_output_parsed(conn):
    r = FakeRunner(envelope({"ok": True, "why": "fine"}))
    v = mk(conn, r).ask("hello", Verdict, purpose="test", system="sys")
    assert v == Verdict(ok=True, why="fine")
    args = r.calls[0]
    assert args[:3] == ["claude", "-p", "hello"]
    for flag in ("--no-session-persistence", "--strict-mcp-config", "--json-schema", "--system-prompt"):
        assert flag in args
    assert args[args.index("--model") + 1] == "haiku"
    assert args[args.index("--tools") + 1] == ""
    assert json.loads(args[args.index("--json-schema") + 1])["required"] == ["ok", "why"]


def test_falls_back_to_result_text_json(conn):
    r = FakeRunner(envelope(None, result='Sure: {"ok": false, "why": "x"}'))
    assert mk(conn, r).ask("q", Verdict, purpose="test", system="s") == Verdict(ok=False, why="x")


def test_retries_once_on_invalid_then_raises(conn):
    r = FakeRunner(envelope({"ok": "maybe"}), envelope({"nope": 1}))
    with pytest.raises(LLMError):
        mk(conn, r).ask("q", Verdict, purpose="test", system="s")
    assert len(r.calls) == 2


def test_retry_succeeds(conn):
    r = FakeRunner(envelope(None, result="not json"), envelope({"ok": True, "why": ""}))
    assert mk(conn, r).ask("q", Verdict, purpose="test", system="s").ok


def test_is_error_raises(conn):
    r = FakeRunner(envelope(None, result="rate limited", is_error=True), envelope(None, result="rate limited", is_error=True))
    with pytest.raises(LLMError, match="rate limited"):
        mk(conn, r).ask("q", Verdict, purpose="test", system="s")


def test_runner_exception_becomes_llm_error(conn):
    r = FakeRunner(TimeoutError("slow"), TimeoutError("slow"))
    with pytest.raises(LLMError):
        mk(conn, r).ask("q", Verdict, purpose="test", system="s")


def test_budget_per_purpose_and_total(conn):
    r = FakeRunner(*[envelope({"ok": True, "why": ""})] * 5)
    llm = mk(conn, r, daily_total=3, per_purpose={"tailor": 1})
    llm.ask("q", Verdict, purpose="tailor", system="s")
    with pytest.raises(BudgetExceeded):
        llm.ask("q", Verdict, purpose="tailor", system="s")
    llm.ask("q", Verdict, purpose="answers", system="s")
    llm.ask("q", Verdict, purpose="answers", system="s")
    with pytest.raises(BudgetExceeded):
        llm.ask("q", Verdict, purpose="answers", system="s")
    assert llm.used() == 3


def test_prompt_too_long(conn):
    with pytest.raises(LLMError, match="too long"):
        mk(conn, FakeRunner()).ask("x" * 30000, Verdict, purpose="test", system="s")


def test_model_override(conn):
    r = FakeRunner(envelope({"ok": True, "why": ""}))
    mk(conn, r).ask("q", Verdict, purpose="t", system="s", model="sonnet")
    assert r.calls[0][r.calls[0].index("--model") + 1] == "sonnet"


def test_purpose_model(conn):
    r = FakeRunner(envelope({"ok": True, "why": ""}), envelope({"ok": True, "why": ""}))
    llm = mk(conn, r, purpose_models={"answers": "sonnet"})
    llm.ask("q", Verdict, purpose="answers", system="s")
    llm.ask("q", Verdict, purpose="fit", system="s")
    assert r.calls[0][r.calls[0].index("--model") + 1] == "sonnet"
    assert r.calls[1][r.calls[1].index("--model") + 1] == "haiku"
