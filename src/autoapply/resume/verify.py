"""Mechanical truth check: a tailored resume (or cover letter) may not state anything the master copy doesn't."""

from __future__ import annotations

import re

from .model import Resume, to_text

# Technology / skill words an LLM might slip in. Any of these in the output must also appear in the master.
TECH_TERMS = [
    "python", "java", "javascript", "typescript", "c", "c++", "c#", "go", "golang", "rust", "scala", "kotlin", "swift",
    "ruby", "php", "perl", "r", "matlab", "julia", "haskell", "ocaml", "f#", "elixir", "dart", "lua", "fortran",
    "sql", "nosql", "postgresql", "postgres", "mysql", "sqlite", "mongodb", "redis", "cassandra", "dynamodb",
    "kdb", "kdb+", "q", "snowflake", "bigquery", "spark", "pyspark", "hadoop", "kafka", "airflow", "dbt", "flink",
    "aws", "gcp", "azure", "docker", "kubernetes", "k8s", "terraform", "ansible", "jenkins", "ci/cd", "github actions",
    "linux", "unix", "bash", "shell", "powershell", "git", "jira",
    "react", "angular", "vue", "node", "node.js", "express", "django", "flask", "fastapi", "spring", "rails",
    ".net", "graphql", "rest", "grpc", "html", "css", "tailwind", "next.js",
    "pandas", "numpy", "scipy", "scikit-learn", "sklearn", "pytorch", "tensorflow", "keras", "jax", "xgboost",
    "lightgbm", "hugging face", "transformers", "langchain", "llm", "llms", "opencv", "cuda", "triton", "mpi",
    "openmp", "fpga", "verilog", "vhdl", "tableau", "power bi", "excel", "vba", "sas", "stata", "spss",
    "jupyter", "matplotlib", "seaborn", "plotly", "d3", "unity", "unreal", "opengl", "vulkan", "arduino", "ros",
    "machine learning", "deep learning", "reinforcement learning", "nlp", "computer vision", "time series",
    "stochastic calculus", "options pricing", "black-scholes", "monte carlo", "kalman", "fix protocol",
    "low-latency", "multithreading", "concurrency", "distributed systems", "microservices", "blockchain",
    "claude code", "polymarket", "stl",
]
_NUM = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?")


def _term_re(term: str) -> re.Pattern:
    return re.compile(rf"(?<![\w+#.]){re.escape(term)}(?![\w+#])", re.IGNORECASE)


_TERM_RES = [(t, _term_re(t)) for t in TECH_TERMS if len(t) > 1 or t in ("c", "r", "q")]


def _single_letter_ok(term: str, text: str) -> bool:
    """'C', 'R', 'Q' as languages only count when capitalised and standalone (not 'a C' in prose... roughly)."""
    return re.search(rf"(?<![\w+#.'-]){term.upper()}(?![\w+#'-])", text) is not None


def verify_text(master_text: str, text: str) -> list[str]:
    problems = []
    master_nums = set(_NUM.findall(master_text))
    for n in sorted(set(_NUM.findall(text)) - master_nums):
        problems.append(f"number not in master: {n}")
    for term, rx in _TERM_RES:
        if len(term) == 1:
            if _single_letter_ok(term, text) and not _single_letter_ok(term, master_text):
                problems.append(f"term not in master: {term}")
        elif rx.search(text) and not rx.search(master_text):
            problems.append(f"term not in master: {term}")
    return problems


def verify(master: Resume, tailored: Resume) -> list[str]:
    problems: list[str] = []
    if (tailored.name, tailored.contact) != (master.name, master.contact):
        problems.append("header: name/contact changed")
    me, te = master.education, tailored.education
    if (te.org, te.location, te.title, te.dates, te.gpa) != (me.org, me.location, me.title, me.dates, me.gpa):
        problems.append("header: education changed")
    problems += [f"skill not in master: {s}" for s in te.skills if s not in me.skills]
    problems += [f"course not in master: {c}" for c in te.coursework if c not in me.coursework]
    msections = {s.id: s for s in master.sections}
    for s in tailored.sections:
        ms = msections.get(s.id)
        if ms is None:
            problems.append(f"unknown section: {s.id}")
            continue
        mentries = {e.id: e for e in ms.entries}
        for e in s.entries:
            me_ = mentries.get(e.id)
            if me_ is None:
                problems.append(f"unknown entry: {e.id}")
                continue
            if (e.org, e.location, e.title, e.dates) != (me_.org, me_.location, me_.title, me_.dates):
                problems.append(f"header changed: {e.id}")
            mids = {b.id for b in me_.bullets}
            problems += [f"unknown bullet: {b.id}" for b in e.bullets if b.id not in mids]
    problems += verify_text(to_text(master), to_text(tailored))
    return problems
