from datetime import datetime, timezone

import httpx
import pytest

from autoapply.sources import boards
from autoapply.sources.base import SourceError

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def client_for(payload, status=200):
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=payload)))


def test_greenhouse_keeps_interns_only():
    payload = {"jobs": [
        {"id": 1, "title": "Software Engineer Intern (Summer 2027)", "absolute_url": "https://job-boards.greenhouse.io/imc/jobs/1",
         "location": {"name": "Chicago, IL"}, "first_published": "2026-09-17T19:59:44-04:00", "company_name": "IMC"},
        {"id": 2, "title": "Senior Network Engineer", "absolute_url": "https://job-boards.greenhouse.io/imc/jobs/2",
         "location": {"name": "Chicago, IL"}, "first_published": None, "company_name": "IMC"},
    ]}
    with client_for(payload) as c:
        [p] = boards.fetch_greenhouse(c, NOW, token="imc", name="IMC")
    assert p.title.startswith("Software Engineer Intern")
    assert p.source == "board:greenhouse:imc" and p.company == "IMC"
    assert p.locations == ("Chicago, IL",)
    assert p.posted_at == datetime.fromisoformat("2026-09-17T19:59:44-04:00")


def test_lever():
    payload = [
        {"id": "u1", "text": "Software Engineer Intern - Summer 2027",
         "hostedUrl": "https://jobs.lever.co/belvederetrading/10746b3d-1760-4573-9b63-b93f5a5e4fc0",
         "categories": {"location": "Chicago, Illinois", "allLocations": ["Chicago, Illinois", "Boulder, CO"]},
         "createdAt": 1790110014068},
        {"id": "u2", "text": "Early Career Talent Partner", "hostedUrl": "https://jobs.lever.co/belvederetrading/x",
         "categories": {}, "createdAt": 1790110014068},
    ]
    with client_for(payload) as c:
        [p] = boards.fetch_lever(c, NOW, token="belvederetrading", name="Belvedere Trading")
    assert p.company == "Belvedere Trading"
    assert p.locations == ("Chicago, Illinois", "Boulder, CO")
    assert p.posted_at == datetime.fromtimestamp(1790110014.068, tz=timezone.utc)


def test_lever_unknown_token_raises():
    with client_for({"ok": False, "error": "Document not found"}) as c, pytest.raises(SourceError):
        boards.fetch_lever(c, NOW, token="nope", name="Nope")


def test_ashby_skips_unlisted():
    payload = {"jobs": [
        {"id": "a", "title": "Machine Learning Intern", "jobUrl": "https://jobs.ashbyhq.com/ramp/b66be397-240b-41a6-9b05-493299b270a9",
         "location": "New York, NY", "secondaryLocations": [{"location": "San Francisco, CA"}],
         "publishedAt": "2026-09-24T13:51:56.459+00:00", "isListed": True},
        {"id": "b", "title": "Data Intern", "jobUrl": "https://jobs.ashbyhq.com/ramp/x", "location": "NY",
         "secondaryLocations": [], "publishedAt": None, "isListed": False},
    ]}
    with client_for(payload) as c:
        [p] = boards.fetch_ashby(c, NOW, token="ramp", name="Ramp")
    assert p.locations == ("New York, NY", "San Francisco, CA")


@pytest.mark.parametrize("title", ["SWE Intern", "Software Engineering Internship", "Data Science Co-op",
                                   "Quant Dev Coop", "Summer 2027 Software Engineer"])
def test_intern_re_matches(title):
    assert boards.INTERN_RE.search(title)


@pytest.mark.parametrize("title", ["Internal Tools Engineer", "International Sales", "Software Engineer"])
def test_intern_re_rejects(title):
    assert not boards.INTERN_RE.search(title)
