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
