from datetime import date
from pathlib import Path

import pytest
from pypdf import PdfReader

from autoapply.llm import LLMError
from autoapply.resume.fit import FitResult
from autoapply.resume.model import Bullet, Resume, load_resume, to_text
from autoapply.resume.render import RenderError, render_pdf
from autoapply.resume.select import ResumeSettings, choose_resume
from autoapply.resume.tailor import Tailoring, TailoredEntry, TailoredSection, TailorError, apply_tailoring
from autoapply.resume.verify import verify, verify_text

MASTER_YAML = """
verified: true
name: Test Person
contact: [t@x.edu, "555"]
education:
  id: u
  org: State University
  location: Town, ST
  title: BS Mathematics
  dates: "Expected: May 2028"
  gpa: "3.92"
  coursework: [Linear Algebra, Statistics]
  skills: [C++, Python, Git]
sections:
  - id: projects
    heading: Projects
    entries:
      - id: lob
        org: C++ Order Book
        location: ""
        title: ""
        dates: May 2025 – Present
        bullets:
          - {id: l1, text: "Built a C++20 matching engine with price-time priority."}
          - {id: l2, text: "Optimized STL containers for constant-time lookup."}
  - id: research
    heading: Research
    entries:
      - id: bot
        org: Lab
        location: Virtual
        title: Researcher
        dates: March 2026 – Present
        bullets:
          - {id: b1, text: "Validated 30+ signals with Sharpe ratios > 2 in Python."}
"""


@pytest.fixture
def master(tmp_path) -> Resume:
    p = tmp_path / "resume.yaml"
    p.write_text(MASTER_YAML, encoding="utf-8")
    return load_resume(p)


def tailoring(**kw):
    base = dict(
        section_order=["research", "projects"],
        sections=[
            TailoredSection(id="research", entries=[TailoredEntry(id="bot", bullets=[
                Bullet(id="b1", text="Validated 30+ trading signals in Python with Sharpe ratios > 2.")])]),
            TailoredSection(id="projects", entries=[TailoredEntry(id="lob", bullets=[
                Bullet(id="l2", text="Optimized STL containers for constant-time order lookup."),
                Bullet(id="l1", text="Built a C++20 matching engine with price-time priority.")])]),
        ],
        skills=["Python", "C++"],
        coursework=["Statistics"],
    )
    base.update(kw)
    return Tailoring(**base)


def test_load_and_text(master):
    assert master.verified and master.education.skills == ["C++", "Python", "Git"]
    t = to_text(master)
    assert "Validated 30+ signals" in t and "State University" in t


def test_apply_and_verify_ok(master):
    t = apply_tailoring(master, tailoring())
    assert [s.id for s in t.sections] == ["research", "projects"]
    assert t.sections[1].entries[0].bullets[0].id == "l2"
    assert t.sections[1].entries[0].dates == "May 2025 – Present"  # headers copied from master
    assert verify(master, t) == []


def test_apply_unknown_id_raises(master):
    bad = tailoring(sections=[TailoredSection(id="research", entries=[TailoredEntry(id="ghost", bullets=[])])])
    with pytest.raises(TailorError):
        apply_tailoring(master, bad)


def test_verify_rejects_invented_skill(master):
    t = apply_tailoring(master, tailoring(skills=["Python", "Rust"]))
    assert any("Rust" in p for p in verify(master, t))


def test_verify_rejects_tech_term_in_bullet(master):
    tl = tailoring()
    tl.sections[1].entries[0].bullets[1] = Bullet(id="l1", text="Built a C++20 matching engine on AWS with Kubernetes.")
    problems = verify(master, apply_tailoring(master, tl))
    assert any("aws" in p.lower() for p in problems) and any("kubernetes" in p.lower() for p in problems)


def test_verify_rejects_new_number(master):
    tl = tailoring()
    tl.sections[0].entries[0].bullets[0] = Bullet(id="b1", text="Validated 45 signals with Sharpe ratios > 2.")
    assert any("45" in p for p in verify(master, apply_tailoring(master, tl)))


def test_verify_rejects_changed_header(master):
    t = apply_tailoring(master, tailoring())
    t.sections[0].entries[0].dates = "2020 – Present"
    assert any("header" in p for p in verify(master, t))


def test_verify_rejects_new_coursework(master):
    t = apply_tailoring(master, tailoring(coursework=["Machine Learning"]))
    assert verify(master, t)


def test_verify_text_for_cover_letters(master):
    assert verify_text(to_text(master), "I built a C++ engine and used Python.") == []
    assert verify_text(to_text(master), "I have 5 years of Java experience.")


def test_render_one_page(master, tmp_path):
    out = render_pdf(master, tmp_path / "r.pdf")
    reader = PdfReader(out)
    assert len(reader.pages) == 1
    assert "Test Person" in reader.pages[0].extract_text()


def test_render_rejects_multi_page(master, tmp_path):
    long = master.model_copy(deep=True)
    long.sections[0].entries[0].bullets = [Bullet(id=f"x{i}", text="Built things. " * 20) for i in range(60)]
    with pytest.raises(RenderError):
        render_pdf(long, tmp_path / "r.pdf")
    assert not (tmp_path / "r.pdf").exists()


class FakeLLM:
    def __init__(self, fit=90, tail=None, fail=False):
        self.fit, self.tail, self.fail = fit, tail, fail
        self.purposes = []

    def ask(self, prompt, schema, *, purpose, system, model=None):
        self.purposes.append(purpose)
        if self.fail:
            raise LLMError("down")
        if schema is FitResult:
            return FitResult(score=self.fit, reason="r")
        return self.tail


def settings(tmp_path, master_yaml):
    pdf = tmp_path / "master.pdf"
    pdf.write_bytes(b"%PDF-1.4 master")
    return ResumeSettings(master_pdf=pdf, master_yaml=master_yaml, out_dir=tmp_path / "out", tailor_threshold=70)


def write_master(tmp_path, verified=True):
    p = tmp_path / "resume.yaml"
    p.write_text(MASTER_YAML.replace("verified: true", f"verified: {str(verified).lower()}"), encoding="utf-8")
    return p


def test_choose_master_when_fit_high(tmp_path):
    s = settings(tmp_path, write_master(tmp_path))
    c = choose_resume(s, company="Acme", title="SWE Intern", description="C++ role", llm=FakeLLM(fit=85))
    assert c.path == s.master_pdf and not c.tailored and c.fit == 85


def test_choose_tailored_when_fit_low(tmp_path):
    s = settings(tmp_path, write_master(tmp_path))
    llm = FakeLLM(fit=40, tail=tailoring())
    c = choose_resume(s, company="Acme Corp", title="Data/ML Intern", description="Python", llm=llm,
                      today=date(2026, 9, 29))
    assert c.tailored, c.reason
    assert c.path.parent == s.out_dir and c.path.name == "Acme_Corp_Data_ML_Intern_2026-09-29.pdf"
    assert len(PdfReader(c.path).pages) == 1
    assert llm.purposes == ["fit", "tailor"]


def test_choose_master_when_unverified(tmp_path):
    s = settings(tmp_path, write_master(tmp_path, verified=False))
    llm = FakeLLM(fit=10)
    c = choose_resume(s, company="A", title="T", description="d", llm=llm)
    assert c.path == s.master_pdf and "not verified" in c.reason and llm.purposes == []


def test_choose_master_when_tailoring_fails_verification(tmp_path):
    s = settings(tmp_path, write_master(tmp_path))
    c = choose_resume(s, company="A", title="T", description="d",
                      llm=FakeLLM(fit=10, tail=tailoring(skills=["Rust"])))
    assert c.path == s.master_pdf and "verify" in c.reason


def test_choose_master_on_llm_error(tmp_path):
    s = settings(tmp_path, write_master(tmp_path))
    c = choose_resume(s, company="A", title="T", description="d", llm=FakeLLM(fail=True))
    assert c.path == s.master_pdf and "down" in c.reason


def test_choose_master_without_description(tmp_path):
    s = settings(tmp_path, write_master(tmp_path))
    llm = FakeLLM()
    c = choose_resume(s, company="A", title="T", description="", llm=llm)
    assert c.path == s.master_pdf and llm.purposes == []


class CoverLLM:
    def __init__(self, paragraphs):
        self.paragraphs = paragraphs

    def ask(self, prompt, schema, *, purpose, system, model=None):
        from autoapply.resume.cover import CoverLetter
        return CoverLetter(paragraphs=self.paragraphs)


def test_cover_letter_ok(master, tmp_path):
    from autoapply.resume.cover import write_cover_letter
    out = write_cover_letter(CoverLLM(["I built a C++20 matching engine.", "I use Python for research."]), master,
                             company="Acme", title="SWE Intern 2027", description="d", out_dir=tmp_path,
                             today=date(2026, 9, 29))
    assert out is not None and out.exists() and "Acme" in PdfReader(out).pages[0].extract_text()


def test_cover_letter_rejects_invented_claims(master, tmp_path):
    from autoapply.resume.cover import write_cover_letter
    out = write_cover_letter(CoverLLM(["I have 3 years of Kubernetes experience."]), master, company="Acme",
                             title="SWE", description="d", out_dir=tmp_path)
    assert out is None
