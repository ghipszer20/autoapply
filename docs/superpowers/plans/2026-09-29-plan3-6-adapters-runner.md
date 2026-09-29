# autoapply Plans 3-6: adapters, runner, scheduling, alerts (as built)

Written after the fact as the record of what was built in the 2026-09-29 session, and why.
Spec: `docs/superpowers/specs/2026-09-29-autoapply-design.md`.

## Pipeline per application (`apply.py::apply_one`)
1. `adapter.load()` turns the live form into a `FormSpec` (fields, types, options, required flags, description).
   A closed posting raises `PostingClosed`, recorded with the final status `closed`.
2. **Rules-only pre-check** (`resolve(..., rules_only=True)`): if a required field is decided by rules alone
   (sensitive or unmappable), the application is skipped before any LLM call.
3. `choose_resume()`: the master PDF, or a tailored PDF once `resume.yaml` has `verified: true` and fit < threshold.
4. `resolve()`: file → sensitive (profile only) → standard profile → answer bank → one batched Haiku draft.
   Conditional follow-ups ("If yes/other, please specify") are left blank unless the parent answer triggers them.
   Free-text drafts that contain numbers or technologies absent from facts, job text and labels are rejected.
   Cover letters are generated last, only for applications that go ahead.
5. `adapter.fill()`, then a full-page screenshot, then `adapter.submit()` only when running live.

## Adapters (`src/autoapply/adapters/`)
| ATS | How | Verified |
|---|---|---|
| Greenhouse | DOM (react-select comboboxes scoped by `react-select-{id}-option-*`; typeahead for school/location) | dry runs on live postings; screenshots reviewed |
| Ashby | schema from public GraphQL `ApiJobPosting`; fill by `[data-field-path]` | dry runs on live postings; screenshots reviewed |
| Lever | `.application-question` blocks, fields by input name; resume uploaded first (Lever autofills) | dry runs on live postings |
| Generic | any single-page form with a resume upload (Workable, JazzHR, BambooHR, Breezy, Rippling, Jobvite) | dry runs |
| Workday | multi-page `apply_flow`; account per tenant (password in Windows Credential Manager) | **pilot**: dry run stops at the account step |

Not supported (they go to `manual` only when they fail; otherwise they're never picked): iCIMS (account logins),
SmartRecruiters (renders nothing to automation), Oracle Cloud / TikTok / ByteDance (login + email codes), Tesla (bot wall).

## Runner (`runner.py`)
- `RunLock` (`data/run.lock`, stale after 2 h) stops overlapping scheduled runs.
- Live runs need `enabled` (kill switch, off by default), which is re-checked before every submission.
- Discovery runs when older than `run.discover_every_hours`. `run.daily_cap` limits submissions per day,
  `run.per_cycle` per pass, and `run.per_company_per_cycle` per company. Submissions are spaced
  `run.spacing_seconds` apart.
- Skipped, deferred and closed applications don't count toward the per-pass target. When the LLM budget runs out,
  the application is `deferred`, not skipped.
- Live runs use real Chrome with a persistent profile (`browser_profile/`); dry runs use headless Chromium.
  PDFs always come from headless Chromium (`render.use_playwright`).

## Scheduling and phone
`autoapply schedule install` registers `pythonw.exe -m autoapply run` every 30 minutes (spike A: never
`cmd.exe`/`python.exe`), with WakeToRun, IgnoreNew and a 2 h limit. Phone commands are in `CLAUDE.md`.

## Rulings (decision — why — cost if wrong)
- Fall 2026 left out of allowed terms — it has already started — about 200 postings skipped.
- "Current company" is answered with the university — the candidate is a full-time student — a recruiter may expect "N/A".
- Off-cycle start dates are estimated (Spring 2027-01-11, Winter 2027-01-04, Fall 2027-08-30) — the profile only has
  summer dates — start dates may be off by days.
- Education start `August 2024` added to profile.yaml — derived from HS graduation May 2024 + class of 2028 — wrong
  start month on forms if not accurate.
- GPA bucket questions truncate (3.912 → 3.9), never round up.
- Class standing is computed per the term a question names (Fall 2027 → Senior) from graduation May 2028.
- Salary questions: text fields get "N/A" (profile rule); number fields get the midpoint of the posted range, or the
  profile fallback hourly rate.
- Government-official / SSN / DOB / age attestations are never answered (skip if required); age notices that only
  ask for an acknowledgement are acknowledged.
- "How did you hear" falls back from "Other" to "Job Board" (the sources are job-board repos).
- Dry passes move on to unseen forms; live passes always take the newest eligible postings first.
