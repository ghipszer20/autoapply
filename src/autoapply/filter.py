"""Rule-based eligibility. Returns (eligible, reason); reason is '' when eligible."""

from __future__ import annotations

import re

from .config import FilterConfig
from .models import Posting

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA",
    "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC", "PR",
}
_STATE_SUFFIX = re.compile(r",\s*([A-Z]{2})\b")
_US_WORDS = re.compile(r"\b(?:USA|United States)\b|\bU\.S\.")


def is_us_location(loc: str) -> bool:
    if _US_WORDS.search(loc):
        return True
    if (m := _STATE_SUFFIX.search(loc)) and m.group(1) in US_STATES:
        return True
    return loc.strip().lower() == "remote"


def evaluate(p: Posting, cfg: FilterConfig) -> tuple[bool, str]:
    if p.category and p.category not in cfg.categories:
        return False, f"category:{p.category}"
    for pat in cfg.title_exclude:
        if re.search(pat, p.title, re.IGNORECASE):
            return False, f"title_exclude:{pat}"
    if not any(re.search(pat, p.title, re.IGNORECASE) for pat in cfg.title_include):
        return False, "title_include:none"
    if p.terms:
        if not any(t in cfg.allowed_terms for t in p.terms):
            return False, f"terms:{','.join(p.terms)}"
    elif re.search(r"\b2026\b", p.title) and "2027" not in p.title:
        return False, "terms:title-2026"
    if cfg.require_bachelors and p.degrees and "Bachelor's" not in p.degrees:
        return False, "degree"
    if cfg.us_only and p.locations and not any(is_us_location(loc) for loc in p.locations):
        return False, "location"
    if not cfg.us_citizen and p.sponsorship == "U.S. Citizenship is Required":
        return False, "citizenship"
    return True, ""
