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

MIN_CONFIDENCE = 0.6
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


def _sponsorship(f, c, s, n):
    now = c.profile.get("work_authorization.requires_sponsorship_now")
    fut = c.profile.get("work_authorization.requires_sponsorship_future")
    if now is None or fut is None:
        return UNANSWERABLE
    return bool(now or fut) if "future" in n or "now" not in n else bool(now)


def _citizen(f, c, s, n):
    cit = c.profile.get("work_authorization.us_citizen")
    if cit is None:
        return UNANSWERABLE
    if f.options and pick_bool(f.options, True) is None:  # status list, not yes/no
        return Choice("U.S. Citizen", ("us citizen", "u s citizen", "united states citizen", "citizen")) if cit else UNANSWERABLE
    return bool(cit)


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


def _hispanic(f, c, s, n):
    v = c.profile.get("eeo.hispanic_or_latino")
    if v is None:
        return UNANSWERABLE
    if f.options and pick_bool(f.options, v) is None:
        return Choice("", ("hispanic or latino",) if v else ("not hispanic or latino", "non hispanic", "no"))
    return v


def _acknowledge(f, c, s, n):
    return True if f.required else BLANK


SENSITIVE: list[Rule] = [
    (_r(r"sponsor|visa|h 1b|h1b|immigration"), _sponsorship),
    (_r(r"export|itar|\bear\b|u s person|us person"), _p("work_authorization.us_person_export_control")),
    (_r(r"clearance"), _clearance),
    (_r(r"authori[sz]ed to work|work authori[sz]ation|eligib\w* to work|legally (able|permitted|entitled)|right to work"),
     _p("work_authorization.authorized_to_work_in_us")),
    (_r(r"citizen"), _citizen),
    (_r(r"sexual orientation"), _eeo("sexual_orientation")),
    (_r(r"transgender"), _eeo("transgender")),
    (_r(r"\bgender\b|\bsex\b"), _eeo("gender")),
    (_r(r"hispanic|latin[oax]"), _hispanic),
    (_r(r"\brace\b|ethnicit"), _eeo("race")),
    (_r(r"veteran|military|armed forces"), _eeo("veteran_status")),
    (_r(r"disabilit"), _eeo("disability_status")),
    # attestations the profile has no answer for: never let the LLM decide
    (_r(r"government official|public official|politically exposed|debarred|sanction|social security|\bssn\b|"
        r"date of birth|\bdob\b|^age$|what is your age|how old"), lambda f, c, s, n: UNANSWERABLE),
    (_r(r"18 years|at least 18|over 18|age of 18|legal age|\b18 or older"), _p("legal_and_checks.over_18")),
    (_r(r"background (check|screen|investigation)"), _p("legal_and_checks.background_check_consent")),
    (_r(r"drug (test|screen)"), _p("legal_and_checks.drug_test_consent")),
    (_r(r"non compete|non solicit|restrictive covenant"), _p("legal_and_checks.non_compete_or_non_solicit")),
    (_r(r"convict|felony|misdemeanor|criminal"), _p("legal_and_checks.criminal_convictions")),
    (_r(r"referr(al|ed|er)?|who referred"), lambda f, c, s, n: False if f.type != "text" or f.required else BLANK),
    (_r(r"relative|family member|related to (an|any)"), _p("legal_and_checks.relatives_at_companies")),
    (_r(r"applied (to|for|with|at)\b.*\b(before|previously|past)|previously applied|applied .*before|past application"),
     lambda f, c, s, n: c.applied_before(s.company)),
    (_r(r"(previously|ever|currently|formerly|former|current) (been )?(employed|worked|an employee)|"
        r"worked (for|at) .*before|former employee|current employee|employee of"),
     _p("legal_and_checks.previously_employed_by_any_company")),
    (_r(r"certify|attest|acknowledge|i agree|agree to|consent|privacy|terms and conditions|accurate|"
        r"true and complete|i understand|read and understand"), _acknowledge),
]


def _addr(part: str) -> Callable:
    return lambda f, c, s, n: c.profile.get(f"identity.address_permanent.{part}")


def _full_name(f, c, s, n):
    return f"{c.profile.get('identity.first_name')} {c.profile.get('identity.last_name')}"


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
    return None if gpa is None else f"{float(gpa):.2f}"


_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]


def _graduation(f, c, s, n):
    grad = str(c.profile.get("education.expected_graduation"))  # e.g. "May 2028"
    m = re.match(r"([A-Za-z]+)\s+(\d{4})", grad)
    if not m:
        return grad
    month, year = m.group(1), m.group(2)
    if "month" in n and "year" not in n:
        return Choice(month, (month, month[:3]))
    if "year" in n and "month" not in n and "school" not in n:
        return Choice(year, (year,))
    if f.type == "date":
        return f"{year}-{_MONTHS.index(month) + 1:02d}-15"
    return Choice(grad, (grad, f"{month[:3]} {year}", year))


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


def _class_standing(f, c, s, n):
    st = c.profile.get("education.class_standing")
    cands = {"Junior": ("junior", "third year", "3rd year", "3", "undergraduate junior")}.get(st, (st,))
    return Choice(st, cands)


def _hear(f, c, s, n):
    h = c.profile.get("preferences.how_did_you_hear") or {}
    if f.type in ("select", "radio", "multiselect"):
        return Choice(h.get("select", "Other"), (h.get("select", "Other"), "other"))
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


STANDARD: list[Rule] = [
    (_r(r"^(legal )?first name|given name|^first$"), _p("identity.first_name")),
    (_r(r"^(legal )?last name|surname|family name|^last$"), _p("identity.last_name")),
    (_r(r"middle name"), _p("identity.middle_name")),
    (_r(r"preferred (first )?name|nickname|name you go by"), _p("identity.preferred_name")),
    (_r(r"^(full |legal |your )?name$|^full legal name"), _full_name),
    (_r(r"e ?mail"), _p("identity.email")),
    (_r(r"phone|mobile|cell"), _p("identity.phone")),
    (_r(r"linkedin"), _p("identity.linkedin")),
    (_r(r"github"), _p("identity.github")),
    (_r(r"website|portfolio|personal (site|url)|other (link|url|profile)"), _p("identity.github")),
    (_r(r"pronoun"), lambda f, c, s, n: Choice(c.profile.get("identity.pronouns"), ("he him", "he/him", "he him his"))),
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
    (_r(r"school|university|college|institution"), _school),
    (_r(r"gpa|grade point"), _gpa),
    (_r(r"graduat|expected (completion|grad)|completion date"), _graduation),
    (_r(r"\bdegree\b"), _degree),
    (_r(r"major|discipline|field of study|area of study|concentration"), _major),
    (_r(r"\bminor\b"), _p("education.minor")),
    (_r(r"start date|earliest (start|available)|available to start|when can you start|availability start"),
     lambda f, c, s, n: _fmt_date(f, _term_dates(c, s)[0])),
    (_r(r"end date|last day|available until"), lambda f, c, s, n: _fmt_date(f, _term_dates(c, s)[1])),
    (_r(r"how did you (hear|find|learn)|where did you (hear|find|learn)|^source$|referral source"), _hear),
    (_r(r"relocat"), lambda f, c, s, n: c.profile.get("preferences.relocate") not in (None, False, "no")),
    (_r(r"salary|compensation|pay (expectation|requirement)|desired (pay|rate)|hourly (rate|pay)|expected pay"), _salary),
    (_r(r"on site|onsite|in office|in person|hybrid|remote|work (mode|arrangement|location preference)"), _work_mode),
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
    "job posting. Never invent experience, skills, employers, numbers, dates, or personal details. Write in the "
    "first person, plainly and specifically, within any max_length. For select/radio questions answer with one "
    "option copied exactly; for multiselect put exact options in choices. If a question cannot be answered "
    "truthfully from FACTS, set unsure=true. confidence is 0-1."
)

_FACT_KEYS = ("education", "availability", "preferences.relocate", "preferences.work_modes", "languages",
              "skills_self_rating", "essays")


def _facts(c: AnswerContext) -> str:
    facts = {k: c.profile.get(k) for k in _FACT_KEYS if c.profile.get(k) not in (None, "pending")}
    return f"RESUME:\n{c.resume_text.strip()}\n\nPROFILE:\n{json.dumps(facts, default=str, indent=1)}"


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


def _accept_draft(f: FormField, d: DraftAnswer | None) -> Any:
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
    if not text or (f.max_length and len(text) > f.max_length):
        return None
    return text


# --- entry point -------------------------------------------------------------------------------------------


def resolve(spec: FormSpec, ctx: AnswerContext) -> Resolution:
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

    for f in spec.fields:
        n = norm(f.label)
        if f.type == "file":
            if not settle(f, _file_rule(f, ctx, spec, n), "file"):
                failed.append(f)
            continue
        if rule := _first_rule(SENSITIVE, n):
            if not settle(f, rule(f, ctx, spec, n), "sensitive"):
                failed.append(f)  # never falls through to the bank or LLM
            continue
        if (rule := _first_rule(STANDARD, n)) and settle(f, rule(f, ctx, spec, n), "profile"):
            continue
        entry = next((b for b in ctx.bank if re.search(b.pattern, f.label, re.I)), None)
        if entry and settle(f, entry.answer, "bank"):
            continue
        unresolved.append(f)

    llm_error = None
    if unresolved and ctx.llm is not None:
        try:
            drafts = _draft(spec, unresolved, ctx)
        except LLMError as e:
            drafts, llm_error = {}, str(e)
        still = []
        for f in unresolved:
            value = _accept_draft(f, drafts.get(f.id))
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
    return res
