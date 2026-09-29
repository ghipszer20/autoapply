# autoapply — automatic internship applications

## Context
Gavin (UMD Applied Math + CS minor, graduating May 2028) wants to apply automatically to about 100 postings a day. Targets: Summer 2027 and off-cycle/co-op internships in SWE, data science, quant dev/SWE and AI/ML. Every application uses the current resume, `Downloads\Hipszer_Resume2026.pdf`.

Decisions made in this conversation:
- **Fully automatic.** There's no per-application review. The phone is used only to turn the process on or off (and check status).
- **No API keys.** All LLM work runs through Claude Code on the Max 5x subscription.
- Built as code in a **private GitHub repo, with no GitHub Actions**. Everything runs on the PC.
- **Sources:** speedyapply/2027-SWE-College-Jobs (favorite), SimplifyJobs and other CS listing repos, company career sites, and LinkedIn/Indeed/Handshake **via their job-alert emails only** (no bots on those accounts).
- **Company sites come first:** an application is always submitted on the company's own application system. A posting that only exists on a third-party site goes to a manual list and is never auto-submitted.

Measured today (SimplifyJobs listings.json, 2026-09-29):
- About 3,290 relevant internships are open, 1,650 of them for Summer 2027.
- About 40–70 relevant new postings per day.
- Application systems: Workday 38%, custom sites 31%, Greenhouse/Ashby/Lever 20%, iCIMS/SmartRecruiters 11%.
- speedyapply links mostly go straight to Greenhouse and Ashby.
- **100 a day is only possible while working through the backlog (about 3–4 weeks). After that, the number of new postings is the limit.**

## Architecture
Python 3.13 project at `C:\Users\24GHi\Code\autoapply` with its own `git init`. It's written in Python because none of it is latency-sensitive. SQLite is the single store.

```
sources/ ──► normalize+dedupe ──► filter ──► prepare (form schema) ──► answers ──► submit (Playwright) ──► log
   ▲                                                                        │
   └─ Task Scheduler (every 30 min, bounded batch)        claude -p (subscription) drafts custom answers
Phone (Claude app) ──Remote Control──► Claude Code session in repo ──► `autoapply on|off|status`
```

### Modules (`src/autoapply/`)
- **`models.py`**: the `Posting` data class (company, title, url, ats, ats_job_id, locations, terms, category, source, posted_at) and the SQLite schema. Tables: postings, applications, answers, events, state (holds the kill switch).
- **`sources/`**: each source has `fetch() -> list[Posting]`.
  - `speedyapply.py`: parses the README markdown/HTML tables of both 2027-SWE-College-Jobs and 2027-AI-College-Jobs, using the "Posting" link.
  - `simplify.py`: reads `.github/scripts/listings.json` from Summer2027-Internships. It already includes off-season terms.
  - `github_lists.py`: generic parser for other repos (vanshb03 etc.), configured in YAML.
  - `company_boards.py`: watchlist YAML of Greenhouse/Lever/Ashby board tokens and Workday tenants, called through their public job APIs. Quant firms go here (Jane Street, Citadel, HRT, Jump, Two Sigma, IMC, Optiver, SIG, DRW, …).
  - `email_alerts.py`: Gmail API with a read-only OAuth scope. It parses LinkedIn/Indeed/Handshake alert emails into (company, title, location) and hands them to the resolver.
- **`resolve.py`**: turns third-party or aggregator links into the company's own application URL. It matches first against postings already found, then searches the company's known job board. Anything it can't resolve is marked `third_party_only` and sent to the manual list. It also produces a canonical URL and the dedupe key (ats + ats_job_id, falling back to normalized company + title).
- **`filter.py`**: rules from `config.yaml`: category/title keywords, terms (Summer 2027, Fall 2026, Spring 2027, co-op), Bachelor's eligible, US locations, exclude PhD-only, clearance-required, hardware and PM roles.
- **`llm.py`**: one wrapper around `claude -p --output-format json --model <m>` (subprocess, on the subscription). It handles timeouts, retries, and a daily call budget.
  - `classify_eligibility(batch)`: Haiku, 20 postings per call, only for postings whose eligibility (class year, degree) the rules can't decide.
  - `draft_answers(posting, questions)`: may use only facts from the resume text and `profile.yaml`. Must return `{answer, confidence}` or `unsure`.
- **`answers.py`**: answers each form field in this order:
  1. Standard fields mapped from `profile.yaml`.
  2. An `answer_bank.yaml` of regex patterns for common questions, approved once at setup.
  3. An LLM draft.
  4. Fixed answers for sensitive questions: work authorization, sponsorship, EEO/demographics (default "decline"), legal attestations, "applied before" and "referral". These are never guessed.
  If any required field ends up unanswered, or the draft is `unsure`, the application is **skipped**, not guessed.
- **`resume/`**: resume tailoring (added 2026-09-29 at the user's request).
  - `resume.yaml` (gitignored) is the structured master copy of `Hipszer_Resume2026.pdf`, turned into data once at setup and checked by the user. Every bullet, skill, course, date and number the resume may ever state lives here. It's the list of facts that tailoring can draw on.
  - `render.py`: HTML template reproducing the current one-page layout, rendered to PDF with Playwright's `page.pdf()`. The PDF must stay one page, or the version is rejected.
  - `fit.py`: `claude -p` (Haiku) scores 0–100 how well the master resume fits the posting. At or above `tailor_threshold` (default 70), the master PDF is used unchanged.
  - `tailor.py`: below the threshold, `claude -p` returns a new `resume.yaml`. It may reorder sections and bullets, choose which coursework/skills/projects to show, and reword bullets to use the posting's terms. It may **not** add skills, technologies, employers, titles, dates, numbers or claims that aren't in the master copy.
  - `verify.py` checks the result mechanically: every skill/technology token and every number in the output must appear in the master copy, and dates, employers and titles must be unchanged. Any failure means the master resume is used instead and the reason is logged.
  - Accepted versions are saved to `C:\Users\24GHi\Downloads\autoapply_resumes\<Company>_<Title>_<YYYY-MM-DD>.pdf`. The applications table records which file was submitted.
  - Usage: tailoring runs only below the threshold. It has its own daily cap in the `claude -p` budget.
- **`ats/`**: one adapter per application system with the same interface: `matches(url)`, `load_form(page, posting) -> FormSpec`, `fill(page, FormSpec, answers)`, `submit(page) -> Result`.
  - Greenhouse (form schema from `boards-api.greenhouse.io/v1/boards/{tok}/jobs/{id}?questions=true`), Ashby, Lever.
  - Later: Workday, iCIMS, SmartRecruiters, and a generic Claude-driven adapter for custom sites.
  - Playwright runs a visible Chrome with a persistent profile. The resume is uploaded from a gitignored `data/` copy.
  - A screenshot of the confirmation page is saved for every submission.
  - A CAPTCHA, or any page it doesn't recognize, sends the application to the manual list. No CAPTCHA solving and no anti-detection tricks.
- **`runner.py` / `cli.py`**:
  - `autoapply run` does one bounded pass: discover every 3 h, then filter → prepare → answer → submit up to N, spaced 2–6 min apart. It stops at the daily cap (100) and at a per-company cap (default 3 per cycle, configurable), and never re-applies to the same job.
  - Commands: `discover`, `run --dry-run` (fills the form and screenshots it but doesn't submit), `on`, `off`, `status`, `digest` (today's submitted/skipped/manual counts with reasons), `manual` (lists third-party-only, CAPTCHA and failed postings with links).

### Scheduling and phone control
- **Windows Task Scheduler** runs `autoapply run` every 30 minutes. Enable "wake the computer to run", and set the laptop power plan so it doesn't sleep while plugged in. The kill switch (the `state.enabled` flag) is checked before every submission.
- **Phone:** a Claude Code session started in the repo with `claude --remote-control`, with push notifications on in `/config`. The repo's `CLAUDE.md` tells that session how to handle "on", "off", "status" and "digest" requests: run the matching CLI command and summarize the result. It can also push a daily digest.
- **Limit:** the PC must be on and the Remote Control session running for phone control to work. Scheduled runs don't depend on that session.

### Repo hygiene (home dir is a git repo with secrets)
- Write `.gitignore` before the first commit, covering: `.env`, `*.db`, `.venv/`, `data/`, `profile.yaml`, `answer_bank.yaml`, `browser_profile/`, `screenshots/`, `logs/`, `credentials*.json`, `token*.json`.
- Commit `profile.example.yaml` and `answer_bank.example.yaml` instead of the real files.
- Check `git rev-parse --show-toplevel` before staging, and review `git diff --cached --name-only` before each commit.
- Create the repo with `gh repo create ghipszer20/autoapply --private`. No `.github/workflows/`.
- Workday passwords go in Windows Credential Manager through `keyring`, never in files.
- After the design is approved, save it to `docs/superpowers/specs/2026-09-29-autoapply-design.md` in the repo.

## Build phases (each ends working and tested)
0. **Spikes (throwaway):**
   - Confirm `claude -p --output-format json` runs from a Task Scheduler job on the subscription, with no API key.
   - Confirm that from the phone, Remote Control can run `autoapply off`.
1. Scaffold: git init, .gitignore, venv, pyproject (httpx, playwright, pyyaml, pydantic, keyring, pytest), private GitHub repo. Also a setup wizard that asks for the profile facts: address, work authorization/sponsorship, EEO choices, availability, links, relocation.
2. Sources (speedyapply, Simplify, company boards) + resolve/dedupe + filter + SQLite. `autoapply discover` prints counts by source, application system and term.
3. `llm.py` + `answers.py` + reviewing the answer bank, plus `resume/` (convert the master to `resume.yaml`, which the user checks; render, fit, tailor, verify; save to `Downloads\autoapply_resumes`).
4. Greenhouse, Ashby and Lever adapters. `--dry-run` on about 10 live postings each, with screenshots checked. Then a live pilot of 5 applications, then turn it on.
5. Runner, caps, kill switch, Task Scheduler job, digest, CLAUDE.md for phone control.
6. Gmail job-alert ingestion (LinkedIn/Indeed/Handshake) resolved to company sites.
7. Workday adapter: create or log in to an account per company, handle email verification through the Gmail API, and step through the multi-page form. This is the biggest coverage gain.
8. iCIMS/SmartRecruiters. Then the generic custom-site adapter, run by Claude through `claude -p` with a Playwright MCP, capped at about 15 a day to protect usage limits.

## Verification
- `pytest`: parsers against saved snapshots of the speedyapply README, listings.json and alert emails; dedupe and company-site priority; filter rules; answer resolver (sensitive questions never reach the LLM, and `unsure` means skip); per-company and daily caps; kill switch.
- Adapters: `autoapply run --dry-run --limit 10 --ats greenhouse` (and likewise for each system), then check the filled-form screenshots before any live submission.
- End to end: a live pilot of 5, then `autoapply digest`. Confirm the confirmation screenshots and that the SQLite audit log records each answer that was submitted.
- Scheduling: a Task Scheduler run while the terminal is closed. Then send "off" from the phone and check the next run submits nothing, and send "on" to resume.
