from datetime import date
from pathlib import Path

import pytest

from autoapply.answers import AnswerContext, BankEntry, DraftAnswer, Drafts, load_bank, resolve
from autoapply.forms import FormField, FormSpec
from autoapply.llm import LLMError
from autoapply.profile import Profile

PROFILE = Profile({
    "identity": {"first_name": "Gavin", "middle_name": "Matthew", "last_name": "Hipszer", "preferred_name": "Gavin",
                 "pronouns": "He/him", "email": "g@umd.edu", "phone": "(443) 555-0100",
                 "github": "https://www.github.com/g", "linkedin": "https://www.linkedin.com/in/g",
                 "address_permanent": {"street": "1 Main St", "city": "Catonsville", "state": "MD", "zip": "21228",
                                       "country": "United States"},
                 "address_school": {"street": "2 Knox Rd", "city": "College Park", "state": "MD", "zip": "20740",
                                    "country": "United States"}},
    "education": {"school": "University of Maryland, College Park", "degree": "Bachelor of Science",
                  "major": "Applied Mathematics", "minor": "Computer Science", "gpa": 3.912,
                  "class_standing": "Junior", "expected_graduation": "May 2028"},
    "work_authorization": {"us_citizen": True, "authorized_to_work_in_us": True, "requires_sponsorship_now": False,
                           "requires_sponsorship_future": False, "us_person_export_control": True,
                           "security_clearance": "none", "clearance_eligible": True},
    "availability": {"summer_2027": {"start": date(2027, 5, 24), "end": date(2027, 8, 20), "weeks": "10-12"},
                     "interested_in_full_time_after_graduation": True},
    "preferences": {"relocate": "anywhere_in_us", "work_mode_preference_order": ["on-site", "hybrid", "remote"],
                    "salary_expectation": {"fallback_hourly_if_no_range": {"software": 22, "quant": 100}},
                    "how_did_you_hear": {"select": "Other", "text": "Personal project"}},
    "legal_and_checks": {"over_18": True, "background_check_consent": True, "drug_test_consent": True,
                         "non_compete_or_non_solicit": False, "criminal_convictions": False,
                         "previously_employed_by_any_company": False, "relatives_at_companies": False},
    "eeo": {"gender": "Male", "race": ["White"], "hispanic_or_latino": False,
            "veteran_status": "Not a protected veteran", "disability_status": "No disability",
            "sexual_orientation": "Heterosexual", "transgender": False},
})
RESUME = Path("resume.pdf")


class FakeLLM:
    def __init__(self, drafts=None, error=None):
        self.drafts = drafts or []
        self.error = error
        self.prompts: list[str] = []

    def ask(self, prompt, schema, *, purpose, system, model=None):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return Drafts(answers=self.drafts)


def ctx(llm=None, bank=(), applied_before=False, cover=None):
    return AnswerContext(profile=PROFILE, resume_pdf=RESUME, resume_text="Gavin Hipszer. C++ order book.",
                         bank=list(bank), llm=llm, applied_before=lambda company: applied_before,
                         cover_letter=cover, today=date(2026, 9, 29))


def spec(*fields, description=""):
    return FormSpec(company="Acme", title="SWE Intern", url="https://x", fields=list(fields), description=description)


def F(id, label, type="text", required=True, options=(), **kw):
    return FormField(id=id, label=label, type=type, required=required, options=tuple(options), **kw)


YN = ("Yes", "No")


def test_standard_fields():
    r = resolve(spec(F("fn", "First Name"), F("ln", "Last Name"), F("em", "Email"), F("ph", "Phone"),
                     F("li", "LinkedIn Profile", required=False), F("cv", "Resume/CV", "file"),
                     F("sch", "School"), F("gpa", "GPA"), F("grad", "Expected graduation date")), ctx())
    assert r.ok, r.skip_reason
    v = {k: a.value for k, a in r.answers.items()}
    assert v["fn"] == "Gavin" and v["ln"] == "Hipszer" and v["em"] == "g@umd.edu"
    assert v["cv"] == RESUME and v["sch"] == "University of Maryland, College Park"
    assert v["gpa"] == "3.91" and v["grad"] == "May 2028"
    assert v["li"] == "https://www.linkedin.com/in/g"


@pytest.mark.parametrize(("label", "options", "want"), [
    ("Will you now or in the future require sponsorship for employment visa status (e.g. H-1B)?", YN, "No"),
    ("Are you legally authorized to work in the United States?", YN, "Yes"),
    ("Are you a U.S. citizen?", YN, "Yes"),
    ("Are you a U.S. Person as defined by export control regulations (ITAR/EAR)?", YN, "Yes"),
    ("Do you currently hold an active security clearance?", YN, "No"),
    ("Are you eligible to obtain a security clearance?", YN, "Yes"),
    ("Gender", ("Female", "Male", "Decline To Self Identify"), "Male"),
    ("Are you Hispanic/Latino?", YN, "No"),
    ("Race", ("Asian", "White", "Two or More Races", "Decline To Self Identify"), "White"),
    ("Veteran Status", ("I am a veteran", "I am not a protected veteran", "I don't wish to answer"),
     "I am not a protected veteran"),
    ("Disability Status", ("Yes, I have a disability", "No, I do not have a disability", "I do not want to answer"),
     "No, I do not have a disability"),
    ("Are you at least 18 years of age?", YN, "Yes"),
    ("Have you ever been convicted of a felony?", YN, "No"),
    ("Have you previously been employed by Acme?", YN, "No"),
    ("Are you subject to a non-compete agreement?", YN, "No"),
    ("Do you have any relatives employed by the company?", YN, "No"),
    ("Were you referred by a current employee?", YN, "No"),
    ("Are you willing to relocate?", YN, "Yes"),
])
def test_sensitive_and_yes_no(label, options, want):
    llm = FakeLLM()
    r = resolve(spec(F("q", label, "select", options=options)), ctx(llm=llm))
    assert r.ok, r.skip_reason
    assert r.answers["q"].value == want
    assert llm.prompts == []  # never reached the LLM


def test_applied_before_uses_db():
    s = spec(F("q", "Have you applied to Acme before?", "select", options=YN))
    assert resolve(s, ctx(applied_before=True)).answers["q"].value == "Yes"
    assert resolve(s, ctx(applied_before=False)).answers["q"].value == "No"


def test_unmappable_sensitive_required_skips_without_llm():
    llm = FakeLLM()
    r = resolve(spec(F("q", "Gender", "select", options=("Option A", "Option B"))), ctx(llm=llm))
    assert not r.ok and "Gender" in r.skip_reason
    assert llm.prompts == []


def test_unknown_attestation_required_skips():
    r = resolve(spec(F("q", "Are you or a family member a government official?", "select", options=YN)), ctx(llm=FakeLLM()))
    assert not r.ok


def test_sensitive_optional_unmappable_left_blank():
    r = resolve(spec(F("q", "Sexual orientation", "select", required=False, options=("A", "B"))), ctx())
    assert r.ok and "q" not in r.answers and r.blank_optional == ["q"]


def test_required_legal_checkbox_checked():
    r = resolve(spec(F("c", "I certify that the information provided is true and complete", "checkbox")), ctx())
    assert r.answers["c"].value is True


def test_optional_marketing_checkbox_left_blank():
    r = resolve(spec(F("c", "I agree to receive text messages about opportunities", "checkbox", required=False)), ctx())
    assert "c" not in r.answers


def test_bank_before_llm():
    bank = [BankEntry(pattern=r"favorite language", answer="C++")]
    llm = FakeLLM()
    r = resolve(spec(F("q", "What is your favorite language?")), ctx(llm=llm, bank=bank))
    assert r.answers["q"].value == "C++" and r.answers["q"].source == "bank"
    assert llm.prompts == []


def test_llm_drafts_free_text_and_select():
    llm = FakeLLM([DraftAnswer(id="why", answer="Because of the order book work.", confidence=0.9),
                   DraftAnswer(id="office", answer="New York", confidence=0.8)])
    s = spec(F("why", "Why do you want to work at Acme?", "textarea"),
             F("office", "Preferred office", "select", options=("Chicago", "New York")),
             F("auth", "Are you legally authorized to work in the US?", "select", options=YN))
    r = resolve(s, ctx(llm=llm))
    assert r.ok, r.skip_reason
    assert r.answers["why"].source == "llm" and r.answers["office"].value == "New York"
    [prompt] = llm.prompts
    assert "Why do you want" in prompt and "authorized" not in prompt


def test_llm_unsure_required_skips():
    llm = FakeLLM([DraftAnswer(id="q", answer="", confidence=0.0, unsure=True)])
    r = resolve(spec(F("q", "Describe your experience with FPGA design")), ctx(llm=llm))
    assert not r.ok and "unanswered" in r.skip_reason


def test_llm_low_confidence_skips():
    llm = FakeLLM([DraftAnswer(id="q", answer="maybe", confidence=0.3)])
    assert not resolve(spec(F("q", "Describe a hard bug you fixed")), ctx(llm=llm)).ok


def test_llm_select_answer_must_be_an_option():
    llm = FakeLLM([DraftAnswer(id="q", answer="Boston", confidence=0.9)])
    r = resolve(spec(F("q", "Preferred office", "select", options=("Chicago", "New York"))), ctx(llm=llm))
    assert not r.ok


def test_llm_answer_too_long_skips():
    llm = FakeLLM([DraftAnswer(id="q", answer="x" * 50, confidence=0.9)])
    assert not resolve(spec(F("q", "Short bio", max_length=20)), ctx(llm=llm)).ok


def test_llm_error_skips_required():
    r = resolve(spec(F("q", "Why us?", "textarea")), ctx(llm=FakeLLM(error=LLMError("down"))))
    assert not r.ok and "llm" in r.skip_reason


def test_no_llm_optional_free_text_blank():
    r = resolve(spec(F("q", "Anything else?", "textarea", required=False)), ctx(llm=None))
    assert r.ok and r.blank_optional == ["q"]


def test_cover_letter_field_uses_generator():
    r = resolve(spec(F("cl", "Cover Letter", "file", required=False)), ctx(cover=lambda s: Path("cl.pdf")))
    assert r.answers["cl"].value == Path("cl.pdf")


def test_unknown_required_file_skips():
    assert not resolve(spec(F("f", "Writing sample", "file")), ctx()).ok


def test_salary_text_na_and_number_from_range():
    desc = "The pay range for this role is $40 - $50/hr."
    r = resolve(spec(F("s", "What are your salary expectations?"), F("n", "Desired hourly pay", "number"),
                     description=desc), ctx())
    assert r.answers["s"].value == "N/A"
    assert r.answers["n"].value == "45"


def test_start_date_and_class_standing():
    r = resolve(spec(F("sd", "Earliest start date", "date"),
                     F("yr", "Current year in school", "select", options=("Freshman", "Sophomore", "Junior", "Senior"))),
                ctx())
    assert r.answers["sd"].value == "2027-05-24"
    assert r.answers["yr"].value == "Junior"


def test_load_bank(tmp_path):
    p = tmp_path / "b.yaml"
    p.write_text("entries:\n  - {pattern: 'favorite color', answer: blue}\n", encoding="utf-8")
    [e] = load_bank(p)
    assert e.answer == "blue"
    assert load_bank(tmp_path / "missing.yaml") == []


def test_followup_blank_when_parent_not_other():
    s = spec(F("h", "How did you hear about us?", "select", options=("LinkedIn", "Other")),
             F("hs", "If you selected other, please specify"),
             F("rel", "Do you have any relatives employed by the company?", "select", options=YN),
             F("reln", "If yes, please provide their name"))
    r = resolve(s, ctx())
    assert r.ok, r.skip_reason
    assert r.answers["h"].value == "Other"
    assert r.answers["hs"].value == "Personal project"  # parent was Other -> profile text
    assert r.answers["rel"].value == "No" and "reln" in r.blank_optional


def test_followup_after_llm_parent_empty_means_blank():
    llm = FakeLLM([DraftAnswer(id="p", answer="No", confidence=0.9),
                   DraftAnswer(id="c", answer="", confidence=1.0, unsure=False)])
    s = spec(F("p", "Have you participated in our fellowship?", "select", options=YN),
             F("c", "If so, which year?"))
    r = resolve(s, ctx(llm=llm))
    assert r.ok and "c" in r.blank_optional


def test_highest_education_and_home_address():
    r = resolve(spec(F("e", "Highest Level of Education", "radio",
                       options=("High School Diploma or GED", "Bachelor's Degree")),
                     F("a", "Home Address", "textarea")), ctx())
    assert r.answers["e"].value == "High School Diploma or GED"
    assert r.answers["a"].value == "1 Main St, Catonsville, MD 21228"


def test_hear_falls_back_to_job_board():
    r = resolve(spec(F("h", "How did you learn about us", "radio", options=("Agency", "Alumni", "Job Board"))), ctx())
    assert r.answers["h"].value == "Job Board"


def test_work_authorization_statements():
    opts = ("Now or at any point the future, I will require visa sponsorship to work in the US.",
            "Now or at any point in the future, I am eligible to work in the United States with no restrictions.")
    r = resolve(spec(F("q", "Please select one of the following statements based on your work authorization:",
                       "select", options=opts)), ctx())
    assert r.answers["q"].value == opts[1]
    opts2 = ("I will require sponsorship", "I do not require sponsorship")
    r2 = resolve(spec(F("q", "Visa sponsorship status", "select", options=opts2)), ctx())
    assert r2.answers["q"].value == opts2[1]


def test_terms_ampersand_single_option():
    r = resolve(spec(F("t", "Terms & Conditions", "select",
                       options=("Yes, I have read and agree to DV Trading's privacy policy",))), ctx())
    assert r.ok and r.answers["t"].value.startswith("Yes")


def test_rules_only_precheck_skips_without_llm_or_cover():
    calls = []
    llm = FakeLLM()
    s = spec(F("g", "Gender", "select", options=("A", "B")), F("cl", "Cover Letter", "file", required=False),
             F("why", "Why us?", "textarea"))
    r = resolve(s, ctx(llm=llm, cover=lambda sp: calls.append(1) or Path("c.pdf")), rules_only=True)
    assert not r.ok and llm.prompts == [] and calls == []
    ok = resolve(spec(F("why", "Why us?", "textarea")), ctx(llm=llm), rules_only=True)
    assert ok.ok and llm.prompts == []


def test_cover_generated_only_when_application_proceeds():
    calls = []
    s = spec(F("cl", "Cover Letter", "file", required=False), F("q", "Why us?", "textarea"))
    r = resolve(s, ctx(llm=FakeLLM([DraftAnswer(id="q", unsure=True)]), cover=lambda sp: calls.append(1) or Path("c.pdf")))
    assert not r.ok and calls == []


def test_single_statement_consent_checkbox():
    stmt = "By applying for this position, I agree that my data will be processed as per Immuta's Privacy Policy."
    r = resolve(spec(F("c", stmt, "multiselect", options=(stmt,))), ctx())
    assert r.ok and r.answers["c"].value == [stmt]
    r2 = resolve(spec(F("c", stmt, "multiselect", required=False, options=("Contact me about future jobs",))), ctx())
    assert "c" in r2.blank_optional


def test_current_company_is_school():
    assert resolve(spec(F("o", "Current company")), ctx()).answers["o"].value == "University of Maryland, College Park"


@pytest.mark.parametrize(("options", "want"), [
    (("On a 4.0 scale.", "4.0", "3.9", "3.8"), "3.9"),
    (("Below 3.0", "3.0 - 3.49", "3.5 - 4.0"), "3.5 - 4.0"),
    (("3.75+", "3.5+", "Below 3.5"), "3.75+"),
])
def test_gpa_buckets(options, want):
    assert resolve(spec(F("g", "What is your current overall GPA?", "select", options=options)), ctx()).answers["g"].value == want


def test_export_control_status_lists():
    o1 = ("U.S. person. This status includes U.S. citizens, U.S. nationals, lawful permanent residents.",
          "Foreign person. This ITAR/EAR status includes anyone who is not a U.S. person (see above).")
    r = resolve(spec(F("e", "The person hired will have access to items subject to U.S. export controls", "select",
                       options=o1)), ctx())
    assert r.answers["e"].value == o1[0]
    o2 = ("A United States Citizen", "A lawful permanent resident of the United States", "A protected individual", "Other")
    r2 = resolve(spec(F("e", "This position requires access to technology subject to export controls, such as EAR. "
                             "Are you any of the following?", "radio", options=o2)), ctx())
    assert r2.answers["e"].value == o2[0]


def test_sponsorship_visa_type_list():
    r = resolve(spec(F("v", "For this specific internship, will you require any of the below sponsorship for employment?",
                       "radio", options=("J1", "F1", "None", "Other"))), ctx())
    assert r.answers["v"].value == "None"


def test_age_notice_acknowledged():
    lab = ("In any materials you submit, you may redact or remove age-identifying information such as age, date of "
           "birth, or dates of school attendance or graduation.")
    r = resolve(spec(F("n", lab, "multiselect", options=("I Acknowledge",))), ctx())
    assert r.ok and r.answers["n"].value == ["I Acknowledge"]


def test_citizenship_country_list():
    r = resolve(spec(F("c", "Please indicate which country is your most recent country of citizenship", "select",
                       options=("Canada", "United States", "Mexico"))), ctx())
    assert r.answers["c"].value == "United States"


@pytest.mark.parametrize("label", ["Please tell us how you heard about this internship opportunity.",
                                   "How did you first learn about Grow Therapy?"])
def test_hear_variants(label):
    r = resolve(spec(F("h", label, "select", options=("Friend", "Other"))), ctx())
    assert r.answers["h"].value == "Other"


def test_llm_text_with_invented_number_or_tech_rejected():
    llm = FakeLLM([DraftAnswer(id="q", answer="I have 5 years of Kubernetes experience.", confidence=0.9)])
    assert not resolve(spec(F("q", "Tell us about yourself", "textarea")), ctx(llm=llm)).ok


def test_llm_text_may_mention_posting_terms():
    llm = FakeLLM([DraftAnswer(id="q", answer="I want to learn Kafka, which your team uses, building on my C++ work.",
                               confidence=0.9)])
    r = resolve(spec(F("q", "Why this role?", "textarea"), description="We use Kafka and Python."), ctx(llm=llm))
    assert r.ok, r.skip_reason


@pytest.mark.parametrize(("label", "want"), [
    ("Current year in school", "Junior"),
    ("What will your academic year be at the start of the Fall 2027 semester?", "Senior"),
    ("Class standing for the 2027-2028 academic year", "Senior"),
    ("Academic year in Spring 2027", "Junior"),
])
def test_class_standing_by_term(label, want):
    r = resolve(spec(F("y", label, "select", options=("Freshman", "Sophomore", "Junior", "Senior"))), ctx())
    assert r.answers["y"].value == want


def test_lgbtq_from_profile():
    r = resolve(spec(F("q", "Do you consider yourself member of the LGBTQIA+ community?", "select", options=YN)), ctx())
    assert r.answers["q"].value == "No"


def test_current_or_former_employee_status_list():
    opts = ("Current Alphabet Employee or Intern", "Former Alphabet Employee or Intern",
            "Current or Former member of Alphabet extended workforce", "Never worked at Alphabet")
    llm = FakeLLM()
    r = resolve(spec(F("q", "Are you a current or former Alphabet employee, intern, vendor, contractor, or temp?", "select",
                       options=opts)), ctx(llm=llm))
    assert r.answers["q"].value == "Never worked at Alphabet" and llm.prompts == []


def test_full_legal_name_has_middle():
    r = resolve(spec(F("n", "Please provide your full legal name as it appears on your government ID"), F("m", "Name")), ctx())
    assert r.answers["n"].value == "Gavin Matthew Hipszer" and r.answers["m"].value == "Gavin Hipszer"


def test_honeypot_stays_blank_even_if_required():
    llm = FakeLLM()
    r = resolve(spec(F("h", "Please leave this field blank"), F("w", "Enter website. This input is for robots only")),
                ctx(llm=llm))
    assert r.ok and "h" not in r.answers and "w" not in r.answers and llm.prompts == []


def test_date_available():
    r = resolve(spec(F("d", "Date Available")), ctx())
    assert r.answers["d"].value == "05/24/2027"


@pytest.mark.parametrize(("label", "want"), [
    ("This position is not currently available for H-1B visa sponsorship. Are you authorized to work in the U.S. "
     "without sponsorship?", "YES"),
    ("Do you now or will you in the future require sponsorship for employment visa status? Please select \"Yes\" if "
     "you will require the Company to commence an immigration case.", "NO"),
    ("This position is in Lincoln, Nebraska. If necessary, are you willing to relocate?", "YES"),
])
def test_question_part_decides(label, want):
    r = resolve(spec(F("q", label, "radio", options=("YES", "NO"))), ctx())
    assert r.answers["q"].value == want
