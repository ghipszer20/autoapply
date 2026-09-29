import pytest

from autoapply.ats import canonicalize, classify

CASES = [
    ("https://job-boards.greenhouse.io/schonfeld/jobs/8180089", "greenhouse", "greenhouse:8180089"),
    ("https://boards.greenhouse.io/figma/jobs/6143238004?gh_jid=6143238004", "greenhouse", "greenhouse:6143238004"),
    ("https://app.careerpuck.com/job-board/lyft/job/8767726002?gh_jid=8767726002", "greenhouse", "greenhouse:8767726002"),
    ("https://job-boards.eu.greenhouse.io/imc/jobs/4979079101", "greenhouse", "greenhouse:4979079101"),
    ("https://boards.greenhouse.io/embed/job_app?for=acme&token=5550001", "greenhouse", "greenhouse:5550001"),
    ("https://jobs.lever.co/belvederetrading/10746b3d-1760-4573-9b63-b93f5a5e4fc0", "lever", "lever:10746b3d-1760-4573-9b63-b93f5a5e4fc0"),
    ("https://jobs.lever.co/belvederetrading/10746b3d-1760-4573-9b63-b93f5a5e4fc0/apply?lever-source=x", "lever", "lever:10746b3d-1760-4573-9b63-b93f5a5e4fc0"),
    ("https://jobs.ashbyhq.com/dryft/3f1c261d-9b65-412b-9f17-34b8968bdd78/application", "ashby", "ashby:3f1c261d-9b65-412b-9f17-34b8968bdd78"),
    ("https://biotechne.wd5.myworkdayjobs.com/en-US/Biotechne/job/San-Jose-CA/Hardware-Engineering-Intern_JR101533", "workday", "workday:biotechne:JR101533"),
    ("https://jainglobal.wd5.myworkdayjobs.com/ExternalSite/job/London-Office/Quant-Research-Intern--Summer-2026---London-_JR100353-1", "workday", "workday:jainglobal:JR100353-1"),
    ("https://wd3.myworkdaysite.com/recruiting/acme/External/job/Remote/SWE-Intern_R123", "workday", "workday:acme:R123"),
    ("https://careers-calamp.icims.com/jobs/4326/intern-engineer/job?mobile=true&needsRedirect=false", "icims", "icims:careers-calamp:4326"),
    ("https://jobs.smartrecruiters.com/Visa/744000012345678-software-engineer-intern", "smartrecruiters", "smartrecruiters:744000012345678"),
    ("https://www.linkedin.com/jobs/view/4012345678/?trk=abc", "third_party", "url:https://www.linkedin.com/jobs/view/4012345678?trk=abc"),
    ("https://app.joinhandshake.com/jobs/9876543", "third_party", "url:https://app.joinhandshake.com/jobs/9876543"),
]


@pytest.mark.parametrize(("url", "ats", "key"), CASES)
def test_classify(url, ats, key):
    ref = classify(url)
    assert (ref.ats, ref.key) == (ats, key)


def test_lever_and_ashby_canonical_drop_apply_suffix():
    assert classify("https://jobs.lever.co/co/10746b3d-1760-4573-9b63-b93f5a5e4fc0/apply").canonical_url == (
        "https://jobs.lever.co/co/10746b3d-1760-4573-9b63-b93f5a5e4fc0"
    )
    assert classify("https://jobs.ashbyhq.com/dryft/3f1c261d-9b65-412b-9f17-34b8968bdd78/application").canonical_url == (
        "https://jobs.ashbyhq.com/dryft/3f1c261d-9b65-412b-9f17-34b8968bdd78"
    )


def test_canonicalize_strips_tracking_and_fragment():
    assert canonicalize("HTTPS://Careers.Example.com/jobs/42/?utm_source=Simplify&ref=Simplify&b=2&a=1#apply") == (
        "https://careers.example.com/jobs/42?a=1&b=2"
    )


def test_other_ats_keyed_by_canonical_url():
    ref = classify("https://www.tesla.com/careers/search/job/intern-software-engineer-123?utm_source=x")
    assert ref.ats == "other"
    assert ref.key == "url:https://www.tesla.com/careers/search/job/intern-software-engineer-123"
