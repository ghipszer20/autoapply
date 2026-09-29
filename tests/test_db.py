from datetime import datetime, timedelta, timezone

from autoapply import db
from autoapply.models import Posting

T0 = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
GH = "https://job-boards.greenhouse.io/schonfeld/jobs/8180089"


def mk(url=GH, source="speedyapply-swe", **kw):
    base = dict(company="Schonfeld", title="2027 Software Engineering Intern", url=url, source=source)
    base.update(kw)
    return Posting(**base)


def test_insert_then_iter(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    stats = db.upsert_postings(conn, [mk(locations=("New York, NY",))], T0)
    assert (stats.new, stats.updated) == (1, 0)
    [(key, p)] = list(db.iter_postings(conn))
    assert key == "greenhouse:8180089"
    assert p.locations == ("New York, NY",)
    assert p.source == "speedyapply-swe"


def test_reupsert_keeps_first_seen(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    db.upsert_postings(conn, [mk()], T0)
    stats = db.upsert_postings(conn, [mk()], T0 + timedelta(hours=3))
    assert (stats.new, stats.updated) == (0, 1)
    row = conn.execute("SELECT first_seen, last_seen, COUNT(*) OVER () AS n FROM postings").fetchone()
    assert row["n"] == 1
    assert row["first_seen"] == T0.isoformat()
    assert row["last_seen"] == (T0 + timedelta(hours=3)).isoformat()


def test_vanished_posting_is_kept(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    db.upsert_postings(conn, [mk()], T0)
    db.upsert_postings(conn, [], T0 + timedelta(days=1))
    assert len(list(db.iter_postings(conn))) == 1


def test_cross_source_same_job_merges(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    db.upsert_postings(
        conn,
        [
            mk(source="speedyapply-swe"),
            mk(url="https://www.schonfeld.com/careers?gh_jid=8180089", source="simplify",
               terms=("Summer 2027",), degrees=("Bachelor's",)),
        ],
        T0,
    )
    [(key, p)] = list(db.iter_postings(conn))
    assert key == "greenhouse:8180089"
    assert p.source == "speedyapply-swe,simplify"
    assert p.terms == ("Summer 2027",)
    assert p.degrees == ("Bachelor's",)
    assert p.url == GH  # first-seen company-ATS URL kept


def test_company_url_replaces_third_party_url(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    li = "https://www.linkedin.com/jobs/view/1"
    db.upsert_postings(conn, [mk(url=li, source="alert")], T0)
    key = "url:" + li
    conn.execute("UPDATE postings SET key=? WHERE key=?", ("greenhouse:8180089", key))
    db.upsert_postings(conn, [mk(source="simplify")], T0)
    row = conn.execute("SELECT ats, url FROM postings").fetchone()
    assert (row["ats"], row["url"]) == ("greenhouse", GH)


def test_eligibility_and_counts(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    db.upsert_postings(conn, [mk(), mk(url="https://jobs.lever.co/x/10746b3d-1760-4573-9b63-b93f5a5e4fc0")], T0)
    db.set_eligibility(conn, [("greenhouse:8180089", True, ""),
                              ("lever:10746b3d-1760-4573-9b63-b93f5a5e4fc0", False, "location")])
    c = db.counts(conn)
    assert c["total"] == 2 and c["eligible"] == 1
    assert c["eligible_by_ats"] == {"greenhouse": 1}
    assert c["top_rejects"] == [("location", 1)]


def test_state_defaults_disabled(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    assert db.is_enabled(conn) is False
    db.set_state(conn, "enabled", "1")
    assert db.is_enabled(conn) is True
    assert db.get_state(conn, "missing", "x") == "x"
