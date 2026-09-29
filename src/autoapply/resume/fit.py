"""How well does the master resume already fit a posting? (0-100, Haiku)."""

from __future__ import annotations

from pydantic import BaseModel, Field

SYSTEM = (
    "You screen internship applications. Score 0-100 how well the RESUME matches the JOB's stated requirements and "
    "focus (skills, domain, level). 70+ means the resume already presents the relevant experience well. Be terse."
)


class FitResult(BaseModel):
    score: int = Field(ge=0, le=100)
    reason: str


def fit_score(llm, resume_text: str, company: str, title: str, description: str) -> FitResult:
    prompt = f"RESUME:\n{resume_text}\n\nJOB: {title} at {company}\n{description[:6000]}"
    return llm.ask(prompt, FitResult, purpose="fit", system=SYSTEM)
