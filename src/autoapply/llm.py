"""The only door to an LLM: `claude -p` on the Max subscription (no API key), structured output, daily budget."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from . import db

T = TypeVar("T", bound=BaseModel)
Runner = Callable[[list[str], int], str]

MAX_PROMPT_CHARS = 24_000  # argv; Windows command lines cap at 32,767 chars
NEUTRAL_CWD = Path(__file__).resolve().parents[2] / "data" / "llm_cwd"  # no CLAUDE.md here


class LLMError(Exception):
    pass


class BudgetExceeded(LLMError):
    pass


def default_runner(args: list[str], timeout: int) -> str:
    """Spike A: windowless, no stdin, own process group — or Task Scheduler kills claude with 0xC000013A."""
    NEUTRAL_CWD.mkdir(parents=True, exist_ok=True)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    proc = subprocess.run(
        args, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
        timeout=timeout, creationflags=flags, cwd=NEUTRAL_CWD,
    )
    if not proc.stdout.strip():
        raise LLMError(f"claude exited {proc.returncode} with no output: {proc.stderr.strip()[:300]}")
    return proc.stdout


def _extract_json(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON object in result")
    return json.loads(m.group(0))


@dataclass
class LLM:
    conn: sqlite3.Connection | None
    runner: Runner = default_runner
    claude_path: str = field(default_factory=lambda: shutil.which("claude") or "claude")
    model: str = "haiku"
    daily_total: int = 400
    per_purpose: dict[str, int] = field(default_factory=dict)
    purpose_models: dict[str, str] = field(default_factory=dict)  # e.g. {"answers": "sonnet"}
    timeout: int = 180
    today: Callable[[], date] = date.today

    def _key(self, purpose: str) -> str:
        return f"llm:{self.today().isoformat()}:{purpose}"

    def used(self, purpose: str | None = None) -> int:
        if self.conn is None:
            return 0
        if purpose is not None:
            return int(db.get_state(self.conn, self._key(purpose), "0"))
        prefix = f"llm:{self.today().isoformat()}:"
        row = self.conn.execute(
            "SELECT COALESCE(SUM(CAST(value AS INTEGER)), 0) FROM state WHERE name LIKE ?", (prefix + "%",)
        ).fetchone()
        return int(row[0])

    def _charge(self, purpose: str) -> None:
        if self.conn is None:
            return
        if self.used() >= self.daily_total:
            raise BudgetExceeded(f"daily LLM budget of {self.daily_total} calls used")
        cap = self.per_purpose.get(purpose)
        if cap is not None and self.used(purpose) >= cap:
            raise BudgetExceeded(f"daily LLM budget for {purpose} ({cap}) used")
        db.set_state(self.conn, self._key(purpose), str(self.used(purpose) + 1))

    def ask(self, prompt: str, schema: type[T], *, purpose: str, system: str, model: str | None = None) -> T:
        if len(prompt) > MAX_PROMPT_CHARS:
            raise LLMError(f"prompt too long ({len(prompt)} chars > {MAX_PROMPT_CHARS})")
        args = [
            self.claude_path, "-p", prompt,
            "--model", model or self.purpose_models.get(purpose) or self.model,
            "--output-format", "json",
            "--tools", "",
            "--no-session-persistence",
            "--setting-sources", "",
            "--strict-mcp-config",
            "--system-prompt", system,
            "--json-schema", json.dumps(schema.model_json_schema()),
        ]
        last = "no attempt"
        for _ in range(2):
            self._charge(purpose)
            try:
                raw = self.runner(args, self.timeout)
                env = json.loads(raw)
                if env.get("is_error"):
                    last = f"claude error: {env.get('result')}"
                    continue
                data = env.get("structured_output")
                if data is None:
                    data = _extract_json(env.get("result") or "")
                return schema.model_validate(data)
            except (ValidationError, ValueError) as e:
                last = f"invalid output: {e}"
            except (OSError, subprocess.SubprocessError, TimeoutError, LLMError) as e:
                if isinstance(e, BudgetExceeded):
                    raise
                last = f"{type(e).__name__}: {e}"
        raise LLMError(f"{purpose}: {last}")
