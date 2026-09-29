import httpx

from autoapply import db
from autoapply.models import Posting
from autoapply.sources.email_alerts import AlertJob, _html_of, _slugs, parse_alert, resolve


def test_parse_linkedin(fixtures):
    jobs = parse_alert("linkedin", (fixtures / "alert_linkedin.html").read_text(encoding="utf-8"))
    assert [(j.job_id, j.title, j.company, j.location) for j in jobs] == [
        ("4012345678", "Software Engineer Intern - Summer 2027", "Acme Robotics", "Boston, MA"),
        ("4099999999", "Data Science Intern", "Beta Analytics", "New York, NY"),
    ]


def test_parse_indeed(fixtures):
    [j] = parse_alert("indeed", (fixtures / "alert_indeed.html").read_text(encoding="utf-8"))
    assert (j.title, j.company, j.location) == ("Machine Learning Intern", "Gamma AI", "San Francisco, CA")


def test_slugs():
    assert _slugs("Acme Robotics, Inc.") == ["acmerobotics", "acme-robotics", "acme"]


def job(**kw):
    base = dict(source="linkedin", job_id="1", title="Software Engineer Intern", company="Acme",
                location="Boston, MA", link="https://www.linkedin.com/comm/jobs/view/1/")
    base.update(kw)
    return AlertJob(**base)


def test_resolve_known_posting_is_dropped(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.upsert_postings(conn, [Posting(company="Acme", title="Software Engineer Intern (Summer 2027)",
                                      url="https://job-boards.greenhouse.io/acme/jobs/5", source="simplify")], None or __import__("datetime").datetime(2026, 9, 29))
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    assert resolve(job(), conn, client) is None


def test_resolve_via_greenhouse_board():
    def handler(r):
        if "boards-api.greenhouse.io/v1/boards/acme/jobs" in str(r.url):
            return httpx.Response(200, json={"jobs": [{"title": "Software Engineer Intern, Summer 2027",
                                                       "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/9"}]})
        return httpx.Response(404)
    p = resolve(job(), None, httpx.Client(transport=httpx.MockTransport(handler)))
    assert p.url == "https://job-boards.greenhouse.io/acme/jobs/9" and p.source == "alert:linkedin"


def test_unresolved_keeps_third_party_link():
    p = resolve(job(), None, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    assert p.url.startswith("https://www.linkedin.com/")


def test_html_of_nested_parts():
    import base64
    data = base64.urlsafe_b64encode(b"<p>hi</p>").decode().rstrip("=")
    payload = {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/plain", "body": {"data": "eA"}}, {"mimeType": "text/html", "body": {"data": data}}]}
    assert _html_of(payload) == "<p>hi</p>"


def test_workday_verify_link_pattern():
    from autoapply.sources.email_alerts import _VERIFY_LINK
    html = '<a href="https://fox.wd1.myworkdayjobs.com/domestic/activate/abc123?redirect=x">Verify Account</a>'
    assert _VERIFY_LINK.search(html).group(0) == "https://fox.wd1.myworkdayjobs.com/domestic/activate/abc123?redirect=x"


def test_greenhouse_code_pattern():
    from autoapply.sources.email_alerts import _GH_CODE
    body = "Copy and paste this security code into the application: aB3dE9fG. It expires in 10 minutes."
    assert _GH_CODE.search(body).group(1) == "aB3dE9fG"
