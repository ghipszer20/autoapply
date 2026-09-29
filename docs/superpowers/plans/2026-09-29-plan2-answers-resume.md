# autoapply Plan 2: LLM wrapper, form answers, resume tailoring

**Goal:** given a posting and its application form (a `FormSpec`), produce every answer the form needs,
or a skip reason. Also pick the resume PDF: the master, or a tailored one that was verified mechanically.
No browser automation here (Plan 3).

**Spec:** `docs/superpowers/specs/2026-09-29-autoapply-design.md` (sections llm.py, answers.py, resume/).

## Decisions
- `claude -p` call shape: from a neutral cwd (the job's `data/llm_cwd`), with `--model haiku --output-format json
  --tools "" --no-session-persistence --setting-sources "" --strict-mcp-config --system-prompt <short>
  --json-schema <schema>`. Measured 2026-09-29: about 4.3k input tokens and about 19 s per call. `structured_output` comes back validated.
  Subprocess flags follow spike A (`stdin=DEVNULL`, `CREATE_NEW_PROCESS_GROUP|CREATE_NO_WINDOW`). The prompt goes in argv,
  capped at 24,000 chars (Windows' command-line limit is 32,767).
- Budget: a daily call counter per purpose, stored in `state` as `llm:<date>:<purpose>`, plus a total cap. It's configured in `config.yaml` under `llm:`.
- Sensitive questions (work authorization, sponsorship, citizenship, export control, clearance, EEO, criminal record, age, background/drug
  checks, non-compete, prior employment, applied before, referral, relatives) are answered **only** from `profile.yaml` or the DB.
  If the options can't be matched, the application is skipped.
- Answer resolution order: file fields → sensitive → standard profile fields → `answer_bank.yaml` → one batched LLM call
  for the rest. A required field that's unanswered or `unsure`, or has confidence below 0.6, makes the application skip.
- `resume.yaml` (gitignored) is converted by hand from the PDF and has a `verified: false` flag. Until the user sets it to `true`,
  tailoring is disabled and the master PDF is always used. This keeps the spec's "checked by the user" requirement without blocking applications.
- Tailored output = the master entries chosen and ordered by id, with bullets that may be reworded but are verified mechanically: no numbers or
  technical terms that aren't in the master copy, and entry headers (org, title, dates) byte-identical. If it renders to more than one page, it's rejected.

## Tasks
1. `llm.py`: `LLM.ask(prompt, schema_model, *, purpose, system, model=None) -> BaseModel`, `LLMError`, `BudgetExceeded`,
   injectable runner; tests use a fake runner (budget, retry on invalid output, is_error handling, argv flags).
2. `profile.py`: `Profile.load(path)`, `get(dotted, default)`, validation of required keys; `profile.example.yaml`.
3. `forms.py`: `FormField`, `FormSpec`, `pick_option(options, want)` (bool/yes-no, EEO wording, fuzzy text).
4. `answers.py` + `answer_bank.example.yaml`: `resolve(spec, ctx) -> Resolution`; tests pin that sensitive questions never reach
   the LLM, that unsure means skip, and cover option mapping for the common Greenhouse/Lever/Ashby wordings.
5. `resume/`: `model.py` (load master), `verify.py`, `render.py` (HTML → PDF via Playwright, one-page check), `fit.py`,
   `tailor.py`, `select.py` (`choose_resume`). Tests: verify rejects invented skills, numbers and changed dates; render stays one page.
6. `config.yaml`: add `llm:` and `resume:` sections; `load_config` models them with defaults.
