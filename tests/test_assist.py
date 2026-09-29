import json
import threading
from datetime import date
from pathlib import Path

from autoapply import db
from autoapply.adapters.base import SubmitResult
from autoapply.apply import apply_one
from autoapply.assist import missing_required, stored_answers, wait_for_user
from autoapply.forms import FormField, FormSpec
from tests.test_apply import FakeAdapter, FakePage, deps

SPEC = FormSpec(company="A", title="T", url="u", fields=[
    FormField(id="fn", label="First Name", type="text", required=True),
    FormField(id="cv", label="Resume", type="file", required=True),
    FormField(id="new", label="New question", type="text", required=True),
])


def test_assist_only_never_submits(tmp_path):
    ad = FakeAdapter(SubmitResult("submitted"))
    o = apply_one(FakePage(), ad, key="fake:1", url="u", company="A", title="T", terms=(), deps=deps(tmp_path),
                  live=True, today=date(2026, 9, 29), assist_only=True)
    assert o.status == "assist" and not ad.submitted and o.answers["fn"]["value"] == "Gavin"


def test_bot_check_routes_to_assist(tmp_path):
    for detail in ("hCaptcha challenge", "flagged as spam by Ashby", "email security code required"):
        o = apply_one(FakePage(), FakeAdapter(SubmitResult("manual", detail)), key="fake:1", url="u", company="A",
                      title="T", terms=(), deps=deps(tmp_path), live=True, today=date(2026, 9, 29))
        assert o.status == "assist", detail


def test_assist_queue_is_not_picked_by_runs(tmp_path):
    from autoapply.models import Posting
    from datetime import datetime, timezone
    conn = db.connect(tmp_path / "t.db")
    db.upsert_postings(conn, [Posting(company="A", title="SWE Intern", url="https://jobs.ashbyhq.com/a/"
                                      "3f1c261d-9b65-412b-9f17-34b8968bdd78", source="s")], datetime(2026, 9, 29, tzinfo=timezone.utc))
    db.set_eligibility(conn, [(k, True, "") for k, _ in db.iter_postings(conn)])
    db.record_application(conn, "ashby:3f1c261d-9b65-412b-9f17-34b8968bdd78", "assist", "x",
                          now=datetime(2026, 9, 29, tzinfo=timezone.utc), answers={"fn": {"value": "G", "source": "profile"}})
    assert db.candidates(conn, ["ashby"], limit=5) == []
    [row] = db.assist_queue(conn)
    assert json.loads(row["answers"])["fn"]["value"] == "G"


def test_stored_answers_and_gap():
    raw = json.dumps({"fn": {"value": "Gavin", "source": "profile"}, "cv": {"value": "C:/r.pdf", "source": "file"},
                      "gone": {"value": "x", "source": "llm"}})
    ans = stored_answers(raw, SPEC)
    assert set(ans) == {"fn", "cv"} and isinstance(ans["cv"].value, Path)
    assert [f.id for f in missing_required(SPEC, ans).fields] == ["new"]


class Page:
    def __init__(self, confirm_after):
        self.n, self.confirm_after, self.url = 0, confirm_after, "https://x/apply"

    def is_closed(self):
        return False

    def inner_text(self, sel):
        self.n += 1
        return "Thank you for applying!" if self.n > self.confirm_after else "form"


def test_wait_for_user():
    assert wait_for_user(Page(2), threading.Event(), seconds=10, sleep=lambda ms: None) == "submitted"
    ev = threading.Event()
    ev.set()
    assert wait_for_user(Page(99), ev, seconds=10, sleep=lambda ms: None) == "skipped"
    assert wait_for_user(Page(99), threading.Event(), seconds=3, sleep=lambda ms: None) == "skipped"
