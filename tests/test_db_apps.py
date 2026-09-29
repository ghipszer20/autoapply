from datetime import datetime, timedelta, timezone

from autoapply import db
from autoapply.models import Posting

T0 = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)


def seed(conn):
    ps = [
        Posting(company="Acme", title="SWE Intern", url="https://job-boards.greenhouse.io/acme/jobs/1", source="s",
                posted_at=T0 - timedelta(days=1)),
        Posting(company="Acme", title="Data Intern", url="https://job-boards.greenhouse.io/acme/jobs/2", source="s",
                posted_at=T0),
        Posting(company="Beta", title="ML Intern", url="https://jobs.lever.co/beta/10746b3d-1760-4573-9b63-b93f5a5e4fc0",
                source="s", posted_at=T0 - timedelta(days=3)),
        Posting(company="Gamma", title="SWE Intern", url="https://gamma.wd5.myworkdayjobs.com/x/job/y_R1", source="s"),
    ]
    db.upsert_postings(conn, ps, T0)
    db.set_eligibility(conn, [(k, True, "") for k, _ in db.iter_postings(conn)])


def test_candidates_order_and_ats_filter(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    seed(conn)
    keys = [c["key"] for c in db.candidates(conn, ["greenhouse", "lever"], limit=10)]
    assert keys == ["greenhouse:2", "greenhouse:1", "lever:10746b3d-1760-4573-9b63-b93f5a5e4fc0"]  # newest first


def test_record_and_exclusions(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    seed(conn)
    db.record_application(conn, "greenhouse:2", "submitted", "ok", now=T0, resume_path="r.pdf", answers={"a": 1})
    db.record_application(conn, "greenhouse:1", "failed", "fill", now=T0)
    keys = [c["key"] for c in db.candidates(conn, ["greenhouse"], limit=10)]
    assert keys == ["greenhouse:1"]  # failed once -> retried
    db.record_application(conn, "greenhouse:1", "failed", "fill", now=T0)
    assert db.candidates(conn, ["greenhouse"], limit=10) == []  # 2 attempts max
    row = conn.execute("SELECT * FROM applications WHERE key='greenhouse:2'").fetchone()
    assert row["status"] == "submitted" and row["submitted_at"] == T0.isoformat() and row["attempts"] == 1


def test_dry_run_does_not_block_live(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    seed(conn)
    db.record_application(conn, "greenhouse:2", "dry_run", "", now=T0)
    assert "greenhouse:2" in [c["key"] for c in db.candidates(conn, ["greenhouse"], limit=10)]


def test_applied_before_and_counts(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    seed(conn)
    assert not db.applied_before(conn, "acme", exclude_key="greenhouse:1")
    db.record_application(conn, "greenhouse:2", "submitted", "", now=T0)
    assert db.applied_before(conn, "ACME", exclude_key="greenhouse:1")
    assert not db.applied_before(conn, "Acme", exclude_key="greenhouse:2")
    assert db.submitted_on(conn, T0.date()) == 1
    assert db.submitted_on(conn, (T0 + timedelta(days=1)).date()) == 0
    d = db.digest(conn, T0.date())
    assert d["counts"] == {"submitted": 1}


def test_reset_skipped(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    seed(conn)
    db.record_application(conn, "greenhouse:1", "skipped", "unanswered: X", now=T0)
    assert db.top_skip_reasons(conn) == [("unanswered: X", 1)]
    assert db.reset_status(conn, "skipped") == 1
    assert "greenhouse:1" in [c["key"] for c in db.candidates(conn, ["greenhouse"], limit=10)]
