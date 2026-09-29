from datetime import date
from pathlib import Path

from autoapply.adapters.base import SubmitResult
from autoapply.answers import BankEntry
from autoapply.apply import ApplyDeps, apply_one
from autoapply.forms import FormField, FormSpec
from autoapply.profile import Profile
from autoapply.resume.select import ResumeSettings
from tests.test_answers import PROFILE


class FakePage:
    closed = False

    def screenshot(self, path, full_page=True, timeout=None):
        Path(path).write_bytes(b"png")

    def is_closed(self):
        return self.closed


class FakeAdapter:
    ats = "fake"

    def __init__(self, submit_result):
        self.submit_result, self.submitted = submit_result, False

    def load(self, page, url, key, company, title):
        return FormSpec(company=company, title=title, url=url, description="",
                        fields=[FormField(id="fn", label="First Name", type="text", required=True)])

    def fill(self, page, spec, answers):
        return []

    def submit(self, page):
        self.submitted = True
        return self.submit_result


def deps(tmp_path):
    pdf = tmp_path / "m.pdf"
    pdf.write_bytes(b"%PDF")
    return ApplyDeps(profile=PROFILE, master=None, master_text="", bank=[],
                     resume_settings=ResumeSettings(pdf, tmp_path / "none.yaml", tmp_path / "out"),
                     llm=None, applied_before=lambda c: False, screenshot_dir=tmp_path / "s", cover_dir=tmp_path / "c")


def run(tmp_path, result, live=True):
    ad = FakeAdapter(result)
    o = apply_one(FakePage(), ad, key="fake:1", url="u", company="A", title="T", terms=(), deps=deps(tmp_path),
                  live=live, today=date(2026, 9, 29))
    return o, ad


def test_dry_run_never_submits(tmp_path):
    o, ad = run(tmp_path, SubmitResult("submitted"), live=False)
    assert o.status == "dry_run" and not ad.submitted and o.answers["fn"]["value"] == "Gavin"


def test_live_submit_confirmed(tmp_path):
    o, ad = run(tmp_path, SubmitResult("submitted", "https://x/confirmation"))
    assert o.status == "submitted" and ad.submitted


def test_unconfirmed_submit_is_final_manual(tmp_path):
    o, _ = run(tmp_path, SubmitResult("failed", "no confirmation"))
    assert o.status == "manual" and "unconfirmed" in o.reason


def test_screenshot_name_is_safe():
    from autoapply.apply import _safe
    assert _safe("url:https://camba.applytojob.com/apply/wv3/Intern") == "url_https_camba.applytojob.com_apply_wv3_Intern"


class CrashyPage(FakePage):
    def screenshot(self, path, full_page=True, timeout=None):
        if full_page:
            raise RuntimeError("renderer crashed")
        Path(path).write_bytes(b"png")


def test_screenshot_failure_never_stops_application(tmp_path):
    ad = FakeAdapter(SubmitResult("submitted", "ok"))
    o = apply_one(CrashyPage(), ad, key="fake:1", url="u", company="A", title="T", terms=(), deps=deps(tmp_path),
                  live=True, today=date(2026, 9, 29))
    assert o.status == "submitted" and o.screenshot.endswith(".png")


def test_closed_tab_before_submit_is_retriable_failure(tmp_path):
    page = FakePage()
    page.closed = True
    ad = FakeAdapter(SubmitResult("submitted"))
    o = apply_one(page, ad, key="fake:1", url="u", company="A", title="T", terms=(), deps=deps(tmp_path), live=True,
                  today=date(2026, 9, 29))
    assert o.status == "failed" and not ad.submitted
