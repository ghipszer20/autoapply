from __future__ import annotations

from functools import partial

from ..config import Config
from . import boards, email_alerts, simplify, speedyapply
from .base import FetchFn

_BOARD_FETCHERS = {"greenhouse": boards.fetch_greenhouse, "lever": boards.fetch_lever, "ashby": boards.fetch_ashby}


def build_sources(cfg: Config, conn=None) -> list[tuple[str, FetchFn]]:
    out: list[tuple[str, FetchFn]] = [(name, partial(speedyapply.fetch, source=name)) for name in speedyapply.LISTS]
    out.append(("simplify", simplify.fetch))
    for ats, entries in cfg.boards.items():
        fetch = _BOARD_FETCHERS[ats]
        out.extend((f"board:{ats}:{tok}", partial(fetch, token=tok, name=name)) for tok, name in entries.items())
    if email_alerts.TOKEN.exists():  # only once the user has run `autoapply gmail-auth`
        out.append(("email-alerts", partial(email_alerts.fetch, conn=conn)))
    return out
