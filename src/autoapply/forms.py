"""Application-form model shared by the ATS adapters and the answer resolver."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

FieldType = Literal["text", "textarea", "select", "multiselect", "radio", "checkbox", "file", "date", "number"]


@dataclass(frozen=True)
class FormField:
    id: str
    label: str
    type: FieldType
    required: bool = False
    options: tuple[str, ...] = ()
    max_length: int | None = None
    description: str = ""


@dataclass
class FormSpec:
    company: str
    title: str
    url: str
    fields: list[FormField]
    description: str = ""  # job description text, for the LLM and pay-range parsing
    meta: dict = field(default_factory=dict)  # adapter-private data


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#]+", " ", s.lower().replace("’", "'").replace("'", ""))).strip()


_YES = re.compile(r"^(yes|y|true|i am|i do|i will|i have|i agree|i accept|i consent|i acknowledge|i certify|i understand)\b")
_NO = re.compile(r"^(no|n|false|i am not|i do not|i dont|i will not|i wont|i have not|i havent|im not)\b")


def pick_bool(options: Sequence[str], want: bool) -> str | None:
    """Choose the yes/no option; with no options (free-text field) answer 'Yes'/'No'."""
    if not options:
        return "Yes" if want else "No"
    for opt in options:
        n = norm(opt)
        is_no = bool(_NO.match(n))  # checked first: "i am not" also starts with "i am"
        is_yes = not is_no and bool(_YES.match(n))
        if (want and is_yes) or (not want and is_no):
            return opt
    return None


def pick(options: Sequence[str], candidates: Sequence[str]) -> str | None:
    """First option matching the earliest candidate: exact, then prefix, then whole-phrase containment."""
    normed = [(o, norm(o)) for o in options]
    for test in (
        lambda n, c: n == c,
        lambda n, c: n.startswith(c + " ") or n == c,
        lambda n, c: re.search(rf"(^| ){re.escape(c)}( |$)", n) is not None,
    ):
        for cand in candidates:
            c = norm(cand)
            for opt, n in normed:
                if c and test(n, c):
                    return opt
    return None
