"""Network / subscription checks. Run with: pytest -m live"""

import pytest
from pydantic import BaseModel

from autoapply.llm import LLM

pytestmark = pytest.mark.live


class Echo(BaseModel):
    word: str


def test_claude_structured_output_roundtrip():
    out = LLM(conn=None).ask("Return the word 'pong' in the field word.", Echo, purpose="live", system="Return JSON only.")
    assert out.word.lower() == "pong"
