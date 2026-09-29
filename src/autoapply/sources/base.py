from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import httpx

from ..models import Posting


class SourceError(Exception):
    """A source returned data we could not parse; never silently yield zero postings."""


FetchFn = Callable[[httpx.Client, datetime], list[Posting]]
