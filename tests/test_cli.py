import io
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from autoapply import cli, db
from autoapply.config import Config, FilterConfig
from autoapply.sources import simplify, speedyapply
from autoapply.sources.registry import build_sources

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)
FIX = Path(__file__).parent / "fixtures"
CFG = Config(
    filter=FilterConfig(categories=["Software", "AI/ML/Data", "Quant"], allowed_terms=["Summer 2027", "Spring 2027"],
                        title_include=[r"software|engineer|data"], title_exclude=[r"\bphd\b"],
                        require_bachelors=True, us_only=True, us_citizen=False),
    boards={"greenhouse": {"imc": "IMC"}},
)


def handler_factory(fail: set[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if any(f in url for f in fail):
            return httpx.Response(500)
        if url == simplify.URL:
            return httpx.Response(200, json=json.loads((FIX / "simplify_sample.json").read_text(encoding="utf-8")))
        if "speedyapply" in url:
            return httpx.Response(200, text=(FIX / "speedyapply_sample.md").read_text(encoding="utf-8"))
        if "greenhouse.io/v1/boards/imc" in url:
            return httpx.Response(200, json={"jobs": []})
        return httpx.Response(404)
    return handler


def run(tmp_path, fail=frozenset()):
    conn = db.connect(tmp_path / "t.db")
    out = io.StringIO()
    with httpx.Client(transport=httpx.MockTransport(handler_factory(set(fail)))) as client:
        code = cli.discover(conn, CFG, client, NOW, out)
    return code, out.getvalue(), conn


def test_build_sources_names():
    names = [n for n, _ in build_sources(CFG)]
    assert names == ["speedyapply-swe", "speedyapply-ai", "simplify", "board:greenhouse:imc"]


def test_discover_stores_dedupes_and_filters(tmp_path):
    code, out, conn = run(tmp_path)
    assert code == 0
    c = db.counts(conn)
    # speedyapply fixture (3 rows) served for both SWE and AI lists -> same keys merge; + 2 simplify
    assert c["total"] == 5
    assert c["eligible"] >= 1
    assert "eligible" in out and "simplify: 2 postings" in out


def test_discover_survives_failing_source(tmp_path):
    code, out, conn = run(tmp_path, fail={"listings.json"})
    assert code == 0
    assert "! simplify" in out
    assert db.counts(conn)["total"] == 3


def test_discover_all_fail_exit_1(tmp_path):
    code, out, _ = run(tmp_path, fail={"speedyapply", "listings.json", "greenhouse"})
    assert code == 1


def test_discover_records_timestamp(tmp_path):
    _, _, conn = run(tmp_path)
    assert db.get_state(conn, "last_discover") == NOW.isoformat()


def test_on_off_status(tmp_path, capsys):
    dbp = str(tmp_path / "s.db")
    assert cli.main(["--db", dbp, "status"]) == 0
    assert "enabled: no" in capsys.readouterr().out
    cli.main(["--db", dbp, "on"])
    cli.main(["--db", dbp, "status"])
    assert "enabled: yes" in capsys.readouterr().out
    cli.main(["--db", dbp, "off"])
    cli.main(["--db", dbp, "status"])
    assert "enabled: no" in capsys.readouterr().out
