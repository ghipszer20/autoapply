# autoapply

Personal pipeline that finds Summer 2027 / off-cycle internships (SWE, data science, quant dev, AI/ML)
and applies automatically on each company's own application system.

Design: `docs/superpowers/specs/2026-09-29-autoapply-design.md`

## How it works
1. **Discover** (every 3 h): speedyapply SWE + AI lists, SimplifyJobs, quant-firm job boards, and (once set up)
   LinkedIn/Indeed/Handshake alert emails. Postings are deduped by ATS job id and filtered by `config.yaml`.
2. **Apply** (every 30 min, up to `run.per_cycle`, `run.daily_cap` per day, 2-6 min apart): for each eligible
   Greenhouse / Ashby / Lever posting it reads the form, answers every field, uploads the resume, and submits.
   - Personal/sensitive answers come only from `profile.yaml` (work authorization, EEO, attestations).
   - Canned answers: `answer_bank.yaml`. Everything else: one `claude -p` call (Haiku, your Max subscription)
     that may only use your resume + profile facts; free text with invented numbers/technologies is rejected.
   - A required question it can't answer truthfully => the application is **skipped**, never guessed.
   - Resume: the original PDF, or (only once `resume.yaml` has `verified: true`) a tailored one-page version that
     is mechanically checked against `resume.yaml`. Saved to `Downloads\autoapply_resumes\`.
   - CAPTCHA challenges, email-verification codes and unknown pages go to the manual list.
3. Every submission is logged in `autoapply.db` (answers + their source) with a screenshot in `screenshots/`.

## Setup
    py -3.13 -m venv .venv
    .venv/Scripts/python -m pip install -e ".[dev]"
    .venv/Scripts/python -m playwright install chromium
    # profile.yaml, answer_bank.yaml, resume.yaml are personal and gitignored

## Use
    .venv/Scripts/autoapply discover                 # fetch + dedupe + filter into autoapply.db
    .venv/Scripts/autoapply run --dry-run --limit 5  # fill forms, screenshot, don't submit
    .venv/Scripts/autoapply apply <key> [--live]     # one posting (key from the DB, e.g. greenhouse:8171692)
    .venv/Scripts/autoapply on|off                   # kill switch (new installs start off)
    .venv/Scripts/autoapply status | digest | manual | gaps
    .venv/Scripts/autoapply retry skipped            # re-try skipped ones after adding answers
    .venv/Scripts/autoapply schedule install         # Task Scheduler: pythonw -m autoapply run, every 30 min

`gaps` lists the questions that most often cause skips: add answers to `profile.yaml` or `answer_bank.yaml`,
then `autoapply retry skipped`.

## Optional: job-alert emails (LinkedIn / Indeed / Handshake)
1. Google Cloud Console -> create a project -> enable the Gmail API -> OAuth consent screen (External, add
   yourself as a test user) -> Credentials -> OAuth client ID -> *Desktop app* -> download JSON.
2. Save it as `data/gmail_credentials.json`, then run `.venv/Scripts/autoapply gmail-auth` once (read-only scope).
Alert jobs are matched to the company's own Greenhouse/Lever/Ashby posting; unmatched ones go to `manual`.

## Phone control
Start `claude --remote-control` in this folder, then enable push notifications in `/config`.
From the Claude app, say "on", "off", "status", "digest", "manual" or "gaps". See CLAUDE.md.
