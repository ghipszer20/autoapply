from datetime import datetime, timedelta, timezone

import httpx
import pytest

from autoapply.sources import speedyapply
from autoapply.sources.base import SourceError

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def load(fixtures):
    return (fixtures / "speedyapply_sample.md").read_text(encoding="utf-8")


def test_parses_all_sections(fixtures):
    ps = speedyapply.parse(load(fixtures), "speedyapply-swe", "Software", NOW)
    assert [p.company for p in ps] == ["Microsoft", "Schonfeld", "AT&T"]
    ms, sch, att = ps
    assert ms.category == "Software" and sch.category == "Quant" and att.category == "Software"
    assert sch.url == "https://job-boards.greenhouse.io/schonfeld/jobs/8180089"
    assert sch.title == "2027 Software Engineering Intern"
    assert sch.posted_at == NOW - timedelta(days=24)
    assert all(p.source == "speedyapply-swe" for p in ps)


def test_entities_and_location_suffix(fixtures):
    att = speedyapply.parse(load(fixtures), "speedyapply-swe", "Software", NOW)[2]
    assert att.company == "AT&T"
    assert att.locations == ("Dallas, TX", "USA")  # list is USA-only; marker lets the US filter pass


def test_no_table_raises():
    with pytest.raises(SourceError):
        speedyapply.parse("# nothing here\n", "speedyapply-swe", "Software", NOW)


def test_header_without_rows_raises():
    md = "### Other\n\n| Company | Position | Location | Posting | Age |\n|---|---|---|---|---|\n"
    with pytest.raises(SourceError):
        speedyapply.parse(md, "speedyapply-swe", "Software", NOW)


def test_fetch_uses_list_url(fixtures):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=load(fixtures))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ps = speedyapply.fetch(client, NOW, source="speedyapply-ai")
    assert seen == [speedyapply.LISTS["speedyapply-ai"][0]]
    assert ps[0].category == "AI/ML/Data"
