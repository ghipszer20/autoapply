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
