"""Shared adapter types: one adapter per application system (Greenhouse, Ashby, Lever, ...)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from ..answers import Resolved
from ..forms import FormSpec, norm

US_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california", "co": "colorado",
    "ct": "connecticut", "de": "delaware", "fl": "florida", "ga": "georgia", "hi": "hawaii", "id": "idaho",
    "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas", "ky": "kentucky", "la": "louisiana",
    "me": "maine", "md": "maryland", "ma": "massachusetts", "mi": "michigan", "mn": "minnesota",
    "ms": "mississippi", "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada",
    "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico", "ny": "new york", "nc": "north carolina",
    "nd": "north dakota", "oh": "ohio", "ok": "oklahoma", "or": "oregon", "pa": "pennsylvania",
    "ri": "rhode island", "sc": "south carolina", "sd": "south dakota", "tn": "tennessee", "tx": "texas",
    "ut": "utah", "vt": "vermont", "va": "virginia", "wa": "washington", "wv": "west virginia",
    "wi": "wisconsin", "wy": "wyoming", "dc": "district of columbia",
}
_STOP = {"of", "the", "at", "and"}


PLACEHOLDER = re.compile(r"^((please )?(select|choose)( one| an option)?(\s*\.\.\.|…)?|-+|—+|)$", re.I)


def real_options(options) -> tuple[str, ...]:
    """Drop placeholder entries such as 'Select...', 'Please select one...', '--'."""
    return tuple(o for o in options if not PLACEHOLDER.match(o.strip()))


def settle(page, timeout: int = 15_000) -> None:
    """Wait for network idle, but never fail on pages that keep polling (analytics, chat widgets)."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:  # noqa: BLE001 - best effort
        page.wait_for_timeout(1500)


class AdapterError(Exception):
    """The page did not look like the form we expected (layout change, closed posting, ...)."""


@dataclass
class SubmitResult:
    status: Literal["submitted", "failed", "manual"]
    detail: str = ""


def _tokens(s: str) -> set[str]:
    out = set()
    for t in norm(s).split():
        out.update(US_STATES.get(t, t).split())
    return out - _STOP


def best_option(options: Sequence[str], value: str) -> str | None:
    """Typeahead choice: an option containing every token of value (state abbreviations expanded), shortest wins."""
    want = _tokens(value)
    if not want:
        return None
    hits = [o for o in options if want <= _tokens(o)]
    return min(hits, key=len) if hits else None


def query_for(value: str) -> str:
    """What to type into a typeahead box: the part before the first comma, at most 4 words."""
    return " ".join(re.split(r"[,(]", value)[0].split()[:4])


class Adapter(Protocol):
    ats: str

    def form_url(self, url: str, key: str) -> str: ...

    def load(self, page, url: str, key: str, company: str, title: str) -> FormSpec: ...

    def fill(self, page, spec: FormSpec, answers: dict[str, Resolved]) -> list[str]: ...

    def submit(self, page) -> SubmitResult: ...
