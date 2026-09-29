"""Answer every field of an application form, or say why the application must be skipped.

Order per field: file uploads -> sensitive (profile/DB only, never the LLM) -> standard profile fields ->
answer_bank.yaml -> one batched LLM draft for whatever is left. A required field left unanswered skips the
application; nothing is guessed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from .forms import FormField, FormSpec, norm, pick, pick_bool
from .llm import LLMError
from .profile import Profile
from .resume.verify import verify_text

MIN_CONFIDENCE = 0.6
CONFIRM_ELIGIBLE = re.compile(r"^(please )?confirm (that )?you (are|have|will be|were|attend|are enrolled)|"
                              r"^are you (a )?(part|member) of (the )?\w+ (scholars?|program|fellowship)")
OVERCLAIM = re.compile(r"\b(proficien\w*|expert(ise)?|extensive(ly)?|mastery|years of (experience|professional)|"
                       r"(strong|solid|deep|advanced) (experience|background|knowledge|skills?|proficiency))\b", re.I)
CONDITIONAL = re.compile(r"^(if (you )?(selected|answered|chose|checked|picked|indicated)\b|if (yes|so|other|applicable)\b|"
                         r"if you (are|have|do|were|will)\b|please (specify|explain|describe) if\b)")
DESCRIPTION_CHARS = 6000


@dataclass
class Resolved:
    value: Any  # str | list[str] | bool | Path
    source: str  # file | sensitive | profile | bank | llm | computed
    confidence: float = 1.0


@dataclass
class Resolution:
    answers: dict[str, Resolved] = field(default_factory=dict)
    skip_reason: str | None = None
    blank_optional: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.skip_reason is None


@dataclass
class BankEntry:
    pattern: str
    answer: Any


@dataclass
class AnswerContext:
    profile: Profile
    resume_pdf: Path
    resume_text: str
    bank: list[BankEntry] = field(default_factory=list)
    llm: Any = None  # autoapply.llm.LLM or a test double with .ask()
    applied_before: Callable[[str], bool] = lambda company: False
    cover_letter: Callable[[FormSpec], Path | str | None] | None = None
    today: date = field(default_factory=date.today)


class DraftAnswer(BaseModel):
    id: str
    answer: str = ""
    choices: list[str] = []
    confidence: float = 0.0
    unsure: bool = False


class Drafts(BaseModel):
    answers: list[DraftAnswer]


def load_bank(path: str | Path) -> list[BankEntry]:
    p = Path(path)
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8-sig")) or {}
    return [BankEntry(pattern=e["pattern"], answer=e["answer"]) for e in raw.get("entries", [])]


# --- value fitting -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Choice:
    """A text answer plus option wordings to try for select/radio fields."""

    text: str
    candidates: tuple[str, ...] = ()


UNANSWERABLE = object()  # sensitive question the profile cannot answer: never fall through to the LLM
BLANK = object()  # leave this (optional) field empty on purpose


def _fit(f: FormField, value: Any) -> Any:
    """Convert a wanted value to what this field accepts, or None if the options don't allow it."""
    if isinstance(value, Path):
        return value if f.type == "file" else None
    if f.type == "file":
        return None
    if isinstance(value, bool):
        if f.type == "checkbox" and not f.options:
            return value
        if f.type in ("select", "radio", "checkbox", "multiselect"):
            opt = pick_bool(f.options, value)
            return None if opt is None else ([opt] if f.type in ("multiselect", "checkbox") else opt)
        return "Yes" if value else "No"
    if isinstance(value, list):
        if f.type == "multiselect" or (f.type == "checkbox" and f.options):
            picked = [pick(f.options, v.candidates or (v.text,)) if isinstance(v, Choice) else pick(f.options, [v])
                      for v in value]
            return None if None in picked else picked
        value = value[0] if value else ""
    if isinstance(value, Choice):
        cands = value.candidates or (value.text,)
        if f.type in ("select", "radio"):
            return pick(f.options, cands)
        if f.type in ("multiselect", "checkbox"):
            opt = pick(f.options, cands)
            return None if opt is None else [opt]
        return value.text
    value = str(value)
    if f.type in ("select", "radio"):
        return pick(f.options, [value])
    if f.type in ("multiselect", "checkbox"):
        opt = pick(f.options, [value])
        return None if opt is None else [opt]
    return value


# --- rules -------------------------------------------------------------------------------------------------

Rule = tuple[re.Pattern, Callable[[FormField, AnswerContext, FormSpec, str], Any]]


def _r(pattern: str) -> re.Pattern:
    return re.compile(pattern)


def _p(key: str) -> Callable:
    return lambda f, c, s, n: c.profile.get(key)


def _clearance(f, c, s, n):
    if re.search(r"eligib|able to obtain|willing|obtain", n):
        return c.profile.get("work_authorization.clearance_eligible")
    held = str(c.profile.get("work_authorization.security_clearance", "none")).lower() not in ("none", "", "false")
    if f.options and not held:
        opt = pick(f.options, ["none", "no clearance", "i do not have a clearance"])
        if opt:
            return Choice(opt, (opt,))
    return held


_NO_SPONSOR = re.compile(r"no restrictions|without (the need for |requiring )?(visa |employer |company )?sponsorship|"
                         r"(do|will) not (now |currently )?(or in the future )?(need|require)|not require|"
                         r"no sponsorship|citizen|permanent resident|green card")
_SPONSOR = re.compile(r"(will|do|would) (now |currently |in the future )?(need|require)|need (visa )?sponsorship|"
                      r"require (visa |employer )?sponsorship")


def _statement(f: FormField, needs_sponsorship: bool) -> Any:
    """Options that are whole statements ('I am eligible ... with no restrictions') rather than yes/no."""
    for opt in f.options:
        n = norm(opt)
        says_no = bool(_NO_SPONSOR.search(n))
        says_yes = not says_no and bool(_SPONSOR.search(n))
        if (not needs_sponsorship and says_no) or (needs_sponsorship and says_yes):
            return Choice(opt, (opt,))
    return None


def _needs_sponsorship(c: AnswerContext) -> bool | None:
    now = c.profile.get("work_authorization.requires_sponsorship_now")
    fut = c.profile.get("work_authorization.requires_sponsorship_future")
    return None if now is None or fut is None else bool(now or fut)


def _sponsorship(f, c, s, n):
    now = c.profile.get("work_authorization.requires_sponsorship_now")
    needs = _needs_sponsorship(c)
    if needs is None:
        return UNANSWERABLE
    if f.options and pick_bool(f.options, needs) is None:
        if st := _statement(f, needs):
            return st
        if not needs:  # visa-type lists: J1 / F1 / H1B / None / Other
            opt = pick(f.options, ("none", "not applicable", "n a", "no sponsorship required", "no sponsorship"))
            if opt:
                return Choice(opt, (opt,))
        return UNANSWERABLE
    return needs if "future" in n or "now" not in n else bool(now)


def _authorized(f, c, s, n):
    ok = c.profile.get("work_authorization.authorized_to_work_in_us")
    if ok is None or (_OTHER_COUNTRY.search(n) and not _US.search(n)):  # e.g. "authorized to work in Canada?"
        return UNANSWERABLE
    if f.options and pick_bool(f.options, bool(ok)) is None and ok:
        needs = _needs_sponsorship(c)
        return (_statement(f, needs) if needs is not None else None) or UNANSWERABLE
    return bool(ok)


_OTHER_COUNTRY = re.compile(r"\b(north korea|syria|iran|cuba|russia|belarus|venezuela|sudan|crimea|china|canada|mexico|"
                            r"india|united kingdom|uk|germany|france|brazil|countries|country list)\b")
_US = re.compile(r"\b(united states|u s|us|usa|america)\b")


def _citizen(f, c, s, n):
    cit = c.profile.get("work_authorization.us_citizen")
    if cit is None:
        return UNANSWERABLE
    if not cit:
        return UNANSWERABLE
    names_others = bool(_OTHER_COUNTRY.search(n))
    if f.options and pick_bool(f.options, True) is None:  # status list or country list, not yes/no
        us_opt = pick(f.options, ("us citizen", "u s citizen", "united states citizen", "united states",
                                  "united states of america", "usa", "citizen"))
        if us_opt is None and all(_OTHER_COUNTRY.search(norm(o)) or len(o) < 30 for o in f.options):
            return BLANK  # a country list without the U.S.: follow-up that doesn't apply to a U.S. citizen
        return Choice(us_opt, (us_opt,)) if us_opt else UNANSWERABLE
    if names_others:  # "Is your country of citizenship one of: North Korea; Syria; ...?"
        return bool(_US.search(n.replace("u s person", "")))
    return True


_EEO_CANDIDATES = {
    "Male": ("male", "man"),
    "Female": ("female", "woman"),
    "Not a protected veteran": ("i am not a protected veteran", "not a protected veteran", "i am not a veteran",
                                "not a veteran", "no"),
    "No disability": ("no i do not have a disability and have not had one in the past", "no i do not have a disability",
                      "no i dont have a disability", "i do not have a disability", "no"),
    "Heterosexual": ("heterosexual", "straight", "heterosexual or straight"),
    "White": ("white",),
}


def _eeo(key: str) -> Callable:
    def rule(f, c, s, n):
        v = c.profile.get(f"eeo.{key}")
        if v is None:
            return UNANSWERABLE
        if isinstance(v, bool):
            return v
        if isinstance(v, list):
            if f.type == "multiselect" or (f.type == "checkbox" and f.options):
                return [Choice(x, _EEO_CANDIDATES.get(x, (x,))) for x in v]
            v = v[0]
        return Choice(v, _EEO_CANDIDATES.get(v, (v,)))
    return rule


def _prior_employment(f, c, s, n):
    worked = c.profile.get("legal_and_checks.previously_employed_by_any_company")
    if worked is None:
        return UNANSWERABLE
    if f.options and pick_bool(f.options, bool(worked)) is None:
        if worked:
            return UNANSWERABLE  # which kind of prior relationship: not something to guess
        opt = pick(f.options, ("never worked", "never", "no", "none", "not applicable"))
        return Choice(opt, (opt,)) if opt else UNANSWERABLE
    return bool(worked)


def _lgbtq(f, c, s, n):
    orientation = c.profile.get("eeo.sexual_orientation")
    trans = c.profile.get("eeo.transgender")
    if orientation is None or trans is None:
        return UNANSWERABLE
    return bool(trans) or str(orientation).lower() not in ("heterosexual", "straight")


def _hispanic(f, c, s, n):
    v = c.profile.get("eeo.hispanic_or_latino")
    if v is None:
        return UNANSWERABLE
    if f.options and pick_bool(f.options, v) is None:
        return Choice("", ("hispanic or latino",) if v else ("not hispanic or latino", "non hispanic", "no"))
    return v


def _notice_or_unanswerable(f, c, s, n):
    """A notice that merely mentions age/DOB and asks for an acknowledgement is fine to acknowledge."""
    if f.options and all(_AGREE.search(norm(o)) for o in f.options) and len(f.options) == 1:
        return _acknowledge(f, c, s, n)
    return UNANSWERABLE


def _export(f, c, s, n):
    """U.S.-person status for ITAR/EAR; handles yes/no and status-list wordings."""
    us_person = c.profile.get("work_authorization.us_person_export_control")
    if us_person is None:
        return UNANSWERABLE
    if not f.options or pick_bool(f.options, bool(us_person)) is not None:
        return bool(us_person)
    if not us_person:
        return UNANSWERABLE
    cands = ("a united states citizen", "united states citizen", "u s citizen", "us citizen") \
        if c.profile.get("work_authorization.us_citizen") else ()
    opt = pick(f.options, (*cands, "u s person", "us person"))
    return Choice(opt, (opt,)) if opt and not re.search(r"\b(foreign|not a u s)\b", norm(opt)) else UNANSWERABLE


_AGREE = re.compile(r"\b(yes|agree|accept|acknowledge|consent|certify|understand|confirm|i have read)\b")


def _acknowledge(f, c, s, n):
    if not f.required:
        return BLANK
    multi = f.type in ("multiselect", "checkbox") and len(f.options) > 1
    if f.options and (multi or pick_bool(f.options, True) is None):
        agreeing = [o for o in f.options if _AGREE.search(norm(o)) and not re.search(r"\b(not|disagree|decline)\b", norm(o))]
        if multi and len(agreeing) > 1:  # several consent boxes: tick those allowed
            allowed = [o for o in agreeing
                       if not (re.search(r"background", norm(o)) and not c.profile.get("legal_and_checks.background_check_consent"))
                       and not (re.search(r"drug", norm(o)) and not c.profile.get("legal_and_checks.drug_test_consent"))
                       and not re.search(r"\b(sms|text messages?|marketing|newsletter)\b", norm(o))]
            return [Choice(o, (o,)) for o in allowed] if allowed else UNANSWERABLE
        if len(f.options) == 1 or len(agreeing) == 1:
            opt = f.options[0] if len(f.options) == 1 else agreeing[0]
            return [Choice(opt, (opt,))] if f.type in ("multiselect", "checkbox") else Choice(opt, (opt,))
        return UNANSWERABLE
    return True


SENSITIVE: list[Rule] = [
    # bot traps: must stay empty even when marked required
    (_r(r"leave (this )?(field )?(blank|empty)|robots? only|for robots|do not (fill|enter|complete)|if you are (a )?human"),
     lambda f, c, s, n: BLANK),
    (_r(r"(authori[sz]ed|eligible|able|permitted) to work\b.*\bwithout\b.*\b(sponsor|visa)"), _authorized),
    (_r(r"sponsor|visa|h 1b|h1b|immigration"), _sponsorship),
    (_r(r"export|itar|\bear\b|u s person|us person"), _export),
    (_r(r"clearance"), _clearance),
    (_r(r"authori[sz]ed to work|authori[sz]ation to work|work authori[sz]ation|eligib\w* to work|"
        r"legally (able|permitted|entitled)|right to work"),
     _authorized),
    (_r(r"citizen"), _citizen),
    (_r(r"sexual orientation"), _eeo("sexual_orientation")),
    (_r(r"lgbt"), _lgbtq),
    (_r(r"transgender"), _eeo("transgender")),
    (_r(r"\bgender\b|\bsex\b"), _eeo("gender")),
    (_r(r"hispanic|latin[oax]"), _hispanic),
    (_r(r"\brace\b|ethnicit"), _eeo("race")),
    (_r(r"veteran|military|armed forces"), _eeo("veteran_status")),
    (_r(r"disabilit"), _eeo("disability_status")),
    # attestations the profile has no answer for: never let the LLM decide
    (_r(r"government official|public official|politically exposed|government employee"),
     _p("legal_and_checks.government_official_self_or_family")),
    (_r(r"debarred|sanction|social security|\bssn\b|"
        r"date of birth|\bdob\b|^age$|what is your age|how old"), lambda f, c, s, n: _notice_or_unanswerable(f, c, s, n)),
    (_r(r"18 years|at least 18|over 18|age of 18|legal age|\b18 or older"), _p("legal_and_checks.over_18")),
    (_r(r"background (check|screen|investigation)"), _p("legal_and_checks.background_check_consent")),
    (_r(r"drug (test|screen)"), _p("legal_and_checks.drug_test_consent")),
    (_r(r"non compete|non solicit|restrictive covenant"), _p("legal_and_checks.non_compete_or_non_solicit")),
    (_r(r"convict|felony|misdemeanor|criminal"), _p("legal_and_checks.criminal_convictions")),
    (_r(r"\breferr(al|ed|er)?\b|\bwho referred"), lambda f, c, s, n: False if f.type != "text" or f.required else BLANK),
    (_r(r"relative|family member|related to (an|any)"), _p("legal_and_checks.relatives_at_companies")),
    (_r(r"applied (to|for|with|at)\b.*\b(before|previously|past)|previously applied|applied .*before|past application"),
     lambda f, c, s, n: c.applied_before(s.company)),
    (_r(r"(previously|ever|currently|formerly|former|current) (been )?(employed|worked|an employee)|"
        r"worked (for|at) .*before|former employee|current employee|employee of|current or former\b|"
        r"former \w+ (employee|intern|contractor)|ever worked (for|at)"),
     _prior_employment),
    (_r(r"certify|attest|acknowledge|i agree|agree to|consent|privacy|terms (and )?conditions|terms of (use|service)|accurate|"
        r"true and complete|i understand|read and understand"), _acknowledge),
]


def _addr(part: str) -> Callable:
    return lambda f, c, s, n: c.profile.get(f"identity.address_permanent.{part}")


def _full_address(f, c, s, n):
    a = c.profile.get("identity.address_permanent") or {}
    return f"{a.get('street')}, {a.get('city')}, {a.get('state')} {a.get('zip')}" if a else None


def _full_name(f, c, s, n):
    parts = [c.profile.get("identity.first_name"), c.profile.get("identity.last_name")]
    if "legal" in n and c.profile.get("identity.middle_name"):  # legal name = as on government ID
        parts.insert(1, c.profile.get("identity.middle_name"))
    return " ".join(p for p in parts if p)


def _school(f, c, s, n):
    school = c.profile.get("education.school")
    return Choice(school, (school, "University of Maryland College Park", "University of Maryland - College Park",
                           "University of Maryland"))


def _degree(f, c, s, n):
    return Choice(c.profile.get("education.degree"), ("bachelor of science", "bachelors", "bachelors degree",
                                                       "bachelor s", "bs", "b s", "bachelor", "undergraduate"))


def _major(f, c, s, n):
    major = c.profile.get("education.major")
    return Choice(major, (major, "mathematics", "math"))


def _gpa(f, c, s, n):
    if "scale" in n or "out of" in n:
        return "4.0"
    gpa = c.profile.get("education.gpa")
    if gpa is None:
        return None
    gpa = float(gpa)
    if f.options:  # bucketed choices: "3.9", "3.5 - 4.0", "Above 3.5", "3.75+"
        for opt in f.options:
            nums = [float(x) for x in re.findall(r"\d\.\d+|\d(?=\.?\s|$)", opt)]
            o = opt.lower()
            if len(nums) >= 2 and nums[0] <= gpa <= nums[1] and nums[0] < nums[1]:
                return Choice(opt, (opt,))
            if len(nums) == 1 and (("+" in o or "above" in o or "or higher" in o or "greater" in o) and gpa >= nums[0]):
                return Choice(opt, (opt,))
        trunc = f"{int(gpa * 10) / 10:.1f}"  # 3.912 -> 3.9 (never round up)
        if exact := pick(f.options, (f"{gpa:.2f}", trunc)):
            return Choice(exact, (exact,))
        for opt in f.options:  # ranges written to one decimal ("3.5 - 3.9") contain the truncated value
            nums = [float(x) for x in re.findall(r"\d\.\d+", opt)]
            if len(nums) >= 2 and nums[0] <= float(trunc) <= nums[1] and nums[0] < nums[1]:
                return Choice(opt, (opt,))
        return None
    return f"{gpa:.2f}"


_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]


def _month_year(f: FormField, n: str, text: str) -> Any:
    """'May 2028' -> the month, the year, a date, or the whole string, depending on what the field asks for."""
    m = re.match(r"([A-Za-z]+)\s+(\d{4})", text)
    if not m:
        return text
    month, year = m.group(1), m.group(2)
    if "month" in n and "year" not in n:
        return Choice(month, (month, month[:3]))
    if "year" in n and "month" not in n and "school" not in n:
        return Choice(year, (year,))
    if f.type == "date":
        return f"{year}-{_MONTHS.index(month) + 1:02d}-15"
    season = {"January": "Winter", "February": "Winter", "March": "Spring", "April": "Spring", "May": "Spring",
              "June": "Summer", "July": "Summer", "August": "Summer", "September": "Fall", "October": "Fall",
              "November": "Fall", "December": "Fall"}.get(month, "")
    return Choice(text, (text, f"{month[:3]} {year}", f"{season} {year}", f"{season} '{year[2:]}", year))


def _graduation(f, c, s, n):
    return _month_year(f, n, str(c.profile.get("education.expected_graduation")))


def _edu_start(f, c, s, n):
    start = c.profile.get("education.start")
    return None if not start else _month_year(f, n, str(start))


_TERM_STARTS = {"Summer 2027": None, "Spring 2027": "2027-01-11", "Winter 2027": "2027-01-04", "Fall 2027": "2027-08-30"}


def _term_dates(c: AnswerContext, s: FormSpec) -> tuple[date, date | None]:
    summer = c.profile.get("availability.summer_2027") or {}
    terms = s.meta.get("terms") or ()
    if terms and "Summer 2027" not in terms:
        for t in terms:
            if _TERM_STARTS.get(t):
                return date.fromisoformat(_TERM_STARTS[t]), None
    return summer.get("start"), summer.get("end")


def _fmt_date(f: FormField, d: date | None) -> Any:
    if d is None:
        return None
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return d.isoformat() if f.type == "date" else d.strftime("%m/%d/%Y")


_STANDING = {  # years of school left after the academic year in question -> standing
    0: ("Senior", ("senior", "fourth year", "4th year", "4", "undergraduate senior", "final year")),
    1: ("Junior", ("junior", "third year", "3rd year", "3", "undergraduate junior")),
    2: ("Sophomore", ("sophomore", "second year", "2nd year", "2", "undergraduate sophomore")),
    3: ("Freshman", ("freshman", "first year", "1st year", "1", "undergraduate freshman")),
}


def _academic_year_end(n: str, today: date) -> int:
    """The spring year that ends the academic year the question is about ('fall 2027' -> 2028)."""
    if m := re.search(r"\b20\d\d (?:20)?(\d\d)\b", n):  # "2027-2028" / "2027-28" (norm turns '-' into ' ')
        return 2000 + int(m.group(1))
    if m := re.search(r"\b(fall|autumn|winter|spring|summer) (20\d\d)\b", n):
        term, year = m.group(1), int(m.group(2))
        return year + 1 if term in ("fall", "autumn", "summer") else year
    return today.year + 1 if today.month >= 8 else today.year


def _class_standing(f, c, s, n):
    grad = re.search(r"(20\d\d)", str(c.profile.get("education.expected_graduation", "")))
    if not grad:
        st = c.profile.get("education.class_standing")
        return Choice(st, (st,)) if st else None
    left = int(grad.group(1)) - _academic_year_end(n, c.today)
    if left not in _STANDING:
        return None
    st, cands = _STANDING[left]
    return Choice(st, cands)


def _hear(f, c, s, n):
    h = c.profile.get("preferences.how_did_you_hear") or {}
    if f.type in ("select", "radio", "multiselect"):
        # exact generic answers first; "Other conferences or events" style categories would be untrue
        exact = [o for o in f.options if norm(o) in ("other", "other please specify", "other please describe",
                                                      "job board", "online job board", "job posting", "internet",
                                                      "internet search", "google", "search engine", "online",
                                                      "company website", "website", "github")]
        if exact:
            order = ["other", "other please specify", "other please describe", "job board", "online job board",
                     "job posting", "internet search", "internet", "search engine", "google", "online", "github",
                     "company website", "website"]
            best = min(exact, key=lambda o: order.index(norm(o)))
            return Choice(best, (best,))
        return Choice(h.get("select", "Other"), (h.get("select", "Other"), "other please", "other ", "job board",
                                                 "online job board", "job posting", "internet", "website", "online"))
    return h.get("text")


_PAY = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(?:k\b)?\s*(?:-|–|—|to)\s*\$?\s?([\d,]+(?:\.\d+)?)\s*(k\b)?", re.I)


def _hourly_midpoint(c: AnswerContext, s: FormSpec) -> float:
    if m := _PAY.search(s.description or ""):
        lo, hi = (float(x.replace(",", "")) for x in m.groups()[:2])
        if m.group(3):
            lo, hi = lo * 1000, hi * 1000
        mid = (lo + hi) / 2
        return mid if mid < 500 else mid / 2080
    fallback = c.profile.get("preferences.salary_expectation.fallback_hourly_if_no_range") or {}
    title = s.title.lower()
    key = "quant" if "quant" in title else "ml" if re.search(r"machine learning|\bml\b|\bai\b", title) else \
        "data_science" if "data" in title else "software"
    return float(fallback.get(key) or fallback.get("software") or 25)


def _salary(f, c, s, n):
    if f.type not in ("number",) and not (f.type in ("select", "radio")):
        return "N/A"
    hourly = _hourly_midpoint(c, s)
    if "hour" in n or "hourly" in n:
        return f"{hourly:.0f}"
    weeks = c.profile.get("availability.summer_2027.weeks", "10-12")
    nums = [int(x) for x in re.findall(r"\d+", str(weeks))] or [12]
    return f"{hourly * 40 * (sum(nums) / len(nums)):.0f}"


def _work_mode(f, c, s, n):
    order = c.profile.get("preferences.work_mode_preference_order") or []
    if f.type in ("select", "radio", "multiselect") and order:
        cands = []
        for m in order:
            cands += {"on-site": ["on site", "onsite", "in office", "in person"], "hybrid": ["hybrid"],
                      "remote": ["remote"]}.get(m, [m])
        return Choice(order[0], tuple(cands))
    return True  # "are you comfortable working on-site/hybrid?" -> all modes acceptable


_LOCAL = re.compile(r"\b(md|maryland|dc|d c|washington dc|washington d c|district of columbia|baltimore|arlington|"
                    r"alexandria|mclean|reston|herndon|tysons|fairfax|chantilly|columbia md|college park|bethesda|"
                    r"rockville|silver spring|annapolis|crystal city)\b")


def _housing(f, c, s, n):
    if c.profile.get("availability.housing") != "needed_unless_local":
        return None
    locs = " ".join(s.meta.get("locations") or ())
    if not locs:
        return UNANSWERABLE if f.required else BLANK  # can't tell whether the office is local
    return not bool(_LOCAL.search(norm(locs)))


def _hours(f, c, s, n):
    part_time = re.search(r"semester|school year|academic year|part time|during (the )?(school|semester)|while (in|attending)",
                          n)
    val = c.profile.get("availability.part_time_hours_per_week" if part_time else "availability.full_time_hours_per_week")
    if val is None:
        return None
    text = str(val)
    if f.type == "number":
        return re.findall(r"\d+", text)[-1]  # "15-20" -> 20 (upper bound of what is available)
    return Choice(text, (text, text.replace("-", " - "), f"{text} hours"))


def _skill_rating(f, c, s, n):
    ratings = {k.lower(): v for k, v in (c.profile.get("skills_self_rating") or {}).items() if isinstance(v, str)}
    hits = [k for k in ratings if re.search(rf"(?<![a-z+#]){re.escape(k)}(?![a-z+#])", n)]
    if len(hits) != 1:
        return None  # several skills in one question: let the resolver/LLM see it whole
    level = ratings[hits[0]]
    return Choice(level, (level, {"Intermediate": "moderate", "Beginner": "basic", "Advanced": "expert"}.get(level, level)))


def _previous_internship(f, c, s, n):
    if c.profile.get("experience.previous_internships") not in ("none", None, False):
        return None
    if f.type == "textarea" or "detail" in n or "describe" in n:
        return ("No prior internships yet. My experience is undergraduate research (NVIDIA 6G Development Program, "
                "FIRE Research Program) and personal projects such as my C++ limit order book.")
    return False


def _offers(f, c, s, n):
    if c.profile.get("experience.current_offers") not in ("none", None, False):
        return None
    if f.options and pick_bool(f.options, False) is None:
        opt = pick(f.options, ("none", "no", "no offers", "not applicable", "n a"))
        return Choice(opt, (opt,)) if opt else None
    return False if f.type != "textarea" else "No, I don't have any offers or offer deadlines right now."


STANDARD: list[Rule] = [
    (_r(r"housing"), _housing),
    (_r(r"interview"), lambda f, c, s, n: c.profile.get("availability.interview_availability")
     if re.search(r"availab|window|times?\b|schedule|when", n) else None),
    (_r(r"hours (per|a|each) week|weekly hours|hours per wk"), _hours),
    (_r(r"proficiency|skill level|experience level|rate your|how would you rate|comfort (level )?with"), _skill_rating),
    (_r(r"(previous|prior|past) internship|completed .{0,30}internship|internship experience"), _previous_internship),
    (_r(r"offer deadline|other offers?|competing offers?|pending offers?|upcoming offer|any offers"), _offers),
    (_r(r"summer 2026"), lambda f, c, s, n: c.profile.get("experience.summer_2026")),
    (_r(r"^(legal )?first name|given name|^first$"), _p("identity.first_name")),
    (_r(r"^(legal )?last name|surname|family name|^last$"), _p("identity.last_name")),
    (_r(r"middle name"), _p("identity.middle_name")),
    (_r(r"preferred (first )?name|nickname|name you go by"), _p("identity.preferred_name")),
    (_r(r"^(full |legal |your )?name$|full legal name|^legal name"), _full_name),
    (_r(r"e ?mail"), _p("identity.email")),
    (_r(r"phone|mobile|cell"), _p("identity.phone")),
    (_r(r"current (company|employer|organi[sz]ation)|^(company|employer|organi[sz]ation)$"),
     lambda f, c, s, n: c.profile.get("education.school")),  # full-time student: the university
    (_r(r"linkedin"), _p("identity.linkedin")),
    (_r(r"github"), _p("identity.github")),
    (_r(r"website|portfolio|personal (site|url)|other (link|url|profile)"), _p("identity.github")),
    (_r(r"pronoun"), lambda f, c, s, n: Choice(c.profile.get("identity.pronouns"), ("he him", "he/him", "he him his"))),
    (_r(r"highest (level of )?(education|degree)|most recently completed|education level completed"),
     lambda f, c, s, n: Choice("High School Diploma", ("high school diploma or ged", "high school diploma",
                                                       "high school or equivalent", "high school", "ged"))),
    (_r(r"(home|mailing|permanent|current|full) address"), _full_address),
    (_r(r"^(street )?address( line 1)?$|street"), _addr("street")),
    (_r(r"^city$|city of residence"), _addr("city")),
    (_r(r"^state|province|region"), lambda f, c, s, n: Choice(c.profile.get("identity.address_permanent.state"),
                                                              (c.profile.get("identity.address_permanent.state"), "Maryland"))),
    (_r(r"zip|postal"), _addr("zip")),
    (_r(r"^country"), lambda f, c, s, n: Choice("United States", ("united states", "united states of america", "usa", "us"))),
    (_r(r"(current )?location|where are you (currently )?(located|based)|current city"),
     lambda f, c, s, n: Choice("College Park, MD", ("College Park, MD", "College Park", "Maryland"))),
    (_r(r"year in school|class (standing|level|year)|academic (year|standing)|current year|year of study"),
     _class_standing),
    (_r(r"education start|(school|university|college) start|enrollment start|start of (your )?(degree|studies)"),
     _edu_start),
    (_r(r"school|university|college|institution"), _school),
    (_r(r"gpa|grade point"), _gpa),
    (_r(r"graduat|expected (completion|grad)|completion date"), _graduation),
    (_r(r"\bdegree\b"), _degree),
    (_r(r"major|discipline|field of study|area of study|concentration"), _major),
    (_r(r"\bminor\b"), _p("education.minor")),
    (_r(r"start date|earliest (start|available)|available to start|when can you start|availability start|"
        r"date available|available (start )?date|availability date|when (are you|would you be) available"),
     lambda f, c, s, n: _fmt_date(f, _term_dates(c, s)[0])),
    (_r(r"end date|last day|available until"), lambda f, c, s, n: _fmt_date(f, _term_dates(c, s)[1])),
    (_r(r"how (did |do )?you (first )?(hear|heard|find|found|learn|learned|discover|come across)|"
        r"where did you (first )?(hear|find|learn|see)|hear about (us|this)|learn about (us|this)|^source$|"
        r"referral source|how were you referred"), _hear),
    (_r(r"relocat"), lambda f, c, s, n: c.profile.get("preferences.relocate") not in (None, False, "no")),
    (_r(r"salary|compensation|pay (expectation|requirement)|desired (pay|rate)|hourly (rate|pay)|expected pay"), _salary),
    (_r(r"work (mode|arrangement|location preference|environment preference)|"
        r"(willing|able|comfortable|open) to work .{0,40}(on site|onsite|in office|in person|hybrid|remote)|"
        r"(on site|onsite|in office|in person|hybrid|remote) (work|role|position|environment|schedule)|"
        r"(prefer|preference).{0,30}(on site|onsite|hybrid|remote)"), _work_mode),
    (_r(r"full time .*after graduat|return offer|interested in full time"),
     _p("availability.interested_in_full_time_after_graduation")),
    (_r(r"currently (enrolled|a student|pursuing)|are you (currently )?(a )?student|enrolled in"), lambda f, c, s, n: True),
]


def _file_rule(f: FormField, c: AnswerContext, s: FormSpec, n: str) -> Any:
    if re.search(r"resume|\bcv\b|curriculum", n):
        return c.resume_pdf
    if "cover" in n:
        out = c.cover_letter(s) if c.cover_letter else None
        return out if out is not None else (UNANSWERABLE if f.required else BLANK)
    if "transcript" in n:
        t = c.profile.get("documents.transcript")
        return Path(t) if t and Path(t).exists() else (UNANSWERABLE if f.required else BLANK)
    return UNANSWERABLE if f.required else BLANK


def _first_rule(rules: list[Rule], n: str) -> Callable | None:
    for pat, fn in rules:
        if pat.search(n):
            return fn
    return None


# --- LLM ---------------------------------------------------------------------------------------------------

SYSTEM = (
    "You fill in internship application questions for the candidate described in FACTS. Use only FACTS and the "
    "job posting. Never invent experience, skills, employers, numbers, dates, or personal details, and never "
    "upgrade a claim: do not add results, impact, users, money, rankings or outcomes that FACTS do not state "
    "(e.g. paper trading is not real trading; a project is not a product with users; an order book project is not "
    "'scalable backend systems'). Never state a skill level (proficient, expert, extensive, strong, solid, advanced): "
    "FACTS rate every language Intermediate, so just name what was used. Dates and availability may only come from "
    "FACTS (availability, graduation); if a question asks for dates FACTS don't give, set unsure=true. Write in the "
    "first person, plainly and specifically, within any max_length. For select/radio questions answer with one "
    "option copied exactly; for multiselect put exact options in choices. Questions starting with 'If ...' depend "
    "on the previous question: if they do not apply given your other answers, return an empty answer with "
    "unsure=false. If a question cannot be answered truthfully from FACTS, set unsure=true. confidence is 0-1."
)

_FACT_KEYS = ("education", "availability", "experience", "preferences.relocate", "preferences.work_modes",
              "languages", "skills_self_rating")


def _facts(c: AnswerContext) -> str:
    facts = {k: c.profile.get(k) for k in _FACT_KEYS if c.profile.get(k) not in (None, "pending")}
    derived = "Available full-time (40 hours per week) during internship terms."  # summer dates are full-time
    return (f"RESUME:\n{c.resume_text.strip()}\n\nPROFILE:\n{json.dumps(facts, default=str, indent=1)}\n"
            f"DERIVED: {derived}")


def _draft(spec: FormSpec, fields: list[FormField], c: AnswerContext) -> dict[str, DraftAnswer]:
    qs = [{"id": f.id, "question": f.label, "type": f.type, "required": f.required,
           **({"options": list(f.options)} if f.options else {}),
           **({"max_length": f.max_length} if f.max_length else {}),
           **({"help": f.description[:300]} if f.description else {})} for f in fields]
    prompt = (
        f"FACTS\n{_facts(c)}\n\nJOB: {spec.title} at {spec.company}\n{(spec.description or '')[:DESCRIPTION_CHARS]}"
        f"\n\nQUESTIONS (answer every id):\n{json.dumps(qs, indent=1)}"
    )
    out = c.llm.ask(prompt, Drafts, purpose="answers", system=SYSTEM)
    return {d.id: d for d in out.answers}


def _accept_draft(f: FormField, d: DraftAnswer | None, allowed_text: str = "") -> Any:
    if d is None or d.unsure or d.confidence < MIN_CONFIDENCE:
        return None
    if f.type in ("multiselect",) or (f.type == "checkbox" and f.options):
        picked = [o for o in d.choices if o in f.options]
        return picked if picked and len(picked) == len(d.choices) else None
    if f.type in ("select", "radio"):
        return d.answer if d.answer in f.options else None
    if f.type == "checkbox":
        return pick_bool([], True) == d.answer
    text = d.answer.strip()
    if f.type == "number":  # "40 hours" -> "40"
        m = re.search(r"\d+(?:\.\d+)?", text)
        text = m.group(0) if m else ""
    if not text or (f.max_length and len(text) > f.max_length):
        return None
    if allowed_text and verify_text(allowed_text, text):  # invented number or technology
        return None
    if OVERCLAIM.search(text):  # skill-level or experience-length claims the profile doesn't support
        return None
    return text


def question_part(label: str) -> str:
    """The sentence(s) that actually ask something: 'Preamble. More context. Are you X?' -> 'Are you X?'."""
    sentences = re.split(r"(?<=[a-z0-9)\"'][.?!])\s+(?=[A-Z\"'(])", label.strip())  # not after 'U.S.'
    asks = [s for s in sentences if s.rstrip().endswith("?")]
    return " ".join(asks) if asks else label


def _triggers_followup(value: Any) -> bool:
    """Does the parent answer open an 'If yes/other, please specify' follow-up?"""
    vals = value if isinstance(value, list) else [value]
    for v in vals:
        if v is True:
            return True
        if isinstance(v, str) and (re.match(r"^(yes|y)\b", norm(v)) or re.search(r"\bother\b", norm(v))):
            return True
    return False


# --- entry point -------------------------------------------------------------------------------------------


def resolve(spec: FormSpec, ctx: AnswerContext, *, rules_only: bool = False) -> Resolution:
    """rules_only: cheap pre-check (no LLM, no cover letter) that only reports rule-decided skips."""
    res = Resolution()
    unresolved: list[FormField] = []
    failed: list[FormField] = []

    def settle(f: FormField, value: Any, source: str) -> bool:
        if value is BLANK:
            res.blank_optional.append(f.id)
            return True
        if value is UNANSWERABLE or value is None:
            return False
        fitted = _fit(f, value)
        if fitted is None or fitted == "" or fitted == []:
            return False
        res.answers[f.id] = Resolved(fitted, source)
        return True

    def by_rules(f: FormField, n: str) -> str:
        """'done' | 'failed' (never goes to the LLM) | 'open' (bank/LLM may answer)."""
        if f.type == "file":
            if "cover" in n:
                deferred.append(f)  # generated only once the application is known to go ahead
                return "deferred"
            return "done" if settle(f, _file_rule(f, ctx, spec, n), "file") else "failed"
        if re.fullmatch(r"(please )?(select|choose)( one| all that apply| an option)?", n) and \
                sum(bool(re.search(r"linkedin|indeed|glassdoor|handshake|career (fair|services)|website|referral|"
                                   r"job board", norm(o))) for o in f.options) >= 2:
            n = "how did you hear about us"  # label lost by the page; the options say what is asked
        # match the actual question first: a preamble ("not eligible for H-1B sponsorship.") must not decide
        # what "Are you authorized to work ... without sponsorship?" asks
        q = norm(question_part(f.label))
        for text in dict.fromkeys((q, n)):
            if rule := _first_rule(SENSITIVE, text):
                return "done" if settle(f, rule(f, ctx, spec, text), "sensitive") else "failed"
        for text in dict.fromkeys((q, n)):
            if (rule := _first_rule(STANDARD, text)) and settle(f, rule(f, ctx, spec, text), "profile"):
                return "done"
        entry = next((b for b in ctx.bank if re.search(b.pattern, f.label, re.I)), None)
        if entry and settle(f, entry.answer, "bank"):
            return "done"
        return "open"

    prev: FormField | None = None
    conditional: set[str] = set()
    deferred: list[FormField] = []
    for f in spec.fields:
        n = norm(f.label)
        parent, prev = prev, f
        if CONDITIONAL.match(n) and parent is not None:
            if parent in unresolved:  # parent is answered by the LLM below: it decides whether this applies
                conditional.add(f.id)
                unresolved.append(f)
                continue
            parent_ans = res.answers.get(parent.id)
            if parent_ans is None or not _triggers_followup(parent_ans.value):
                res.blank_optional.append(f.id)
                continue
            if _first_rule(STANDARD, norm(parent.label)) is _hear and settle(
                    f, (ctx.profile.get("preferences.how_did_you_hear") or {}).get("text"), "profile"):
                continue
        outcome = by_rules(f, n)
        if outcome == "failed":
            failed.append(f)
        elif outcome == "open":
            unresolved.append(f)

    hard = next((f for f in failed if f.required), None)
    if hard is not None:
        res.skip_reason = f"unanswered: {hard.label[:80]}"
        return res
    if rules_only:
        return res

    llm_error = None
    allowed = "\n".join([_facts(ctx), spec.company, spec.title, spec.description or "", *(f.label for f in spec.fields)])
    if unresolved and ctx.llm is not None:
        try:
            drafts = _draft(spec, unresolved, ctx)
        except LLMError as e:
            drafts, llm_error = {}, str(e)
        still = []
        for f in unresolved:
            d = drafts.get(f.id)
            if f.id in conditional and d is not None and not d.unsure and not (d.answer.strip() or d.choices):
                res.blank_optional.append(f.id)  # follow-up that does not apply
                continue
            value = _accept_draft(f, d, allowed)
            if value is None:
                still.append(f)
            else:
                d = drafts[f.id]
                res.answers[f.id] = Resolved(value, "llm", d.confidence)
        unresolved = still

    for f in failed + unresolved:
        if f.required:
            why = f"llm error ({llm_error})" if llm_error and f in unresolved else "unanswered"
            res.skip_reason = f"{why}: {f.label[:80]}"
            return res
        if f.id not in res.blank_optional:
            res.blank_optional.append(f.id)
    for f in spec.fields:  # "Please confirm you are part of X program" answered No: not eligible, don't apply
        a = res.answers.get(f.id)
        if a is not None and f.required and CONFIRM_ELIGIBLE.search(norm(f.label)):
            v = a.value[0] if isinstance(a.value, list) and a.value else a.value
            if v is False or (isinstance(v, str) and re.match(r"^(no|not)\b", norm(v))):
                res.skip_reason = f"not eligible: {f.label[:80]}"
                return res
    for f in deferred:
        if not settle(f, _file_rule(f, ctx, spec, norm(f.label)), "file"):
            if f.required:
                res.skip_reason = f"unanswered: {f.label[:80]}"
                return res
            res.blank_optional.append(f.id)
    return res
