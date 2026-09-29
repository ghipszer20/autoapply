import pytest

from autoapply.config import FilterConfig
from autoapply.filter import evaluate, is_us_location
from autoapply.models import Posting

CFG = FilterConfig(
    categories=["Software", "AI/ML/Data", "Quant"],
    allowed_terms=["Summer 2027", "Fall 2027", "Spring 2027", "Winter 2027"],
    title_include=[r"software|\bswe\b|developer|engineer|programm",
                   r"data|machine learning|\bml\b|\bai\b|artificial intelligence|analytics"],
    title_exclude=[r"\bph\.?d\b|\bmasters?\b|master's|\bmba\b", r"hardware|mechanical", r"\btrader\b"],
    require_bachelors=True, us_only=True, us_citizen=False,
)


def P(**kw):
    base = dict(company="Acme", title="Software Engineer Intern", url="https://x/1", source="simplify",
                locations=("Austin, TX",), terms=("Summer 2027",), category="Software", degrees=("Bachelor's",))
    base.update(kw)
    return Posting(**base)


def test_happy_path():
    assert evaluate(P(), CFG) == (True, "")


@pytest.mark.parametrize(("kw", "reason"), [
    (dict(category="Hardware"), "category:Hardware"),
    (dict(title="Machine Learning Intern - PhD"), "title_exclude"),
    (dict(title="Quantitative Trader Intern"), "title_exclude"),
    (dict(title="Finance Intern"), "title_include"),
    (dict(terms=("Summer 2026",)), "terms"),
    (dict(terms=(), title="Software Engineer Intern - Fall 2026"), "terms"),
    (dict(degrees=("Master's", "PhD")), "degree"),
    (dict(locations=("London, UK",)), "location"),
    (dict(locations=("Toronto, ON",)), "location"),
    (dict(sponsorship="U.S. Citizenship is Required"), "citizenship"),
])
def test_rejections(kw, reason):
    ok, why = evaluate(P(**kw), CFG)
    assert not ok and why.startswith(reason)


def test_quant_dev_passes_via_engineer_pattern():
    assert evaluate(P(title="Quantitative Developer Intern", category="Quant"), CFG)[0]


def test_empty_category_and_terms_rely_on_title():
    assert evaluate(P(category="", terms=(), title="SWE Intern - Summer 2027"), CFG) == (True, "")


def test_citizen_allows_citizenship_roles():
    cfg = CFG.model_copy(update={"us_citizen": True})
    assert evaluate(P(sponsorship="U.S. Citizenship is Required"), cfg)[0]


@pytest.mark.parametrize(("loc", "us"), [
    ("Austin, TX", True), ("Washington, DC", True), ("Remote in USA", True), ("Remote", True),
    ("USA", True), ("United States", True), ("Anywhere in the U.S.", True), ("London, UK", False), ("Toronto, ON", False),
    ("Remote in Canada", False), ("APAC", False),
])
def test_is_us_location(loc, us):
    assert is_us_location(loc) is us
