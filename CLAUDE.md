# autoapply — operating instructions for Claude Code sessions

This repo runs an automatic internship-application pipeline. The user usually talks to this
session from the Claude mobile app through Remote Control. Keep replies to one or two short lines.

## Phone commands
Run commands from the repo root with `.venv/Scripts/autoapply`:
- "on" / "start" / "resume": run `.venv/Scripts/autoapply on`, then `.venv/Scripts/autoapply status`
- "off" / "stop" / "pause": run `.venv/Scripts/autoapply off`, then `.venv/Scripts/autoapply status`
- "status" / "how's it going": run `.venv/Scripts/autoapply status` and summarize in one line
- "digest" / "what did you apply to today": run `.venv/Scripts/autoapply digest` and summarize
  (counts per status, then the submitted companies)
- "manual" / "what needs me": run `.venv/Scripts/autoapply manual` and list the first 10 lines
- "gaps" / "why are you skipping": run `.venv/Scripts/autoapply gaps` and list the top 5 reasons
- "assist" / "what needs me": run `.venv/Scripts/autoapply status` and report the "waiting for you" count;
  finishing them (`autoapply assist`) needs the user at the PC because they click Submit themselves

Never edit code, config, or git state in response to a phone command. If a request is anything other
than the commands above, say so and ask the user to do it from the PC.

## Development rules
- Own git repo at this folder; the parent home dir is a separate repo with secrets. Check
  `git rev-parse --show-toplevel` before staging and review `git diff --cached --name-only`.
- Private GitHub repo; no GitHub Actions.
- Tests: `.venv/Scripts/python -m pytest -q` (network-free); live checks: `-m live`.
- Personal files are gitignored and must stay that way: profile.yaml, answer_bank.yaml, resume.yaml,
  autoapply.db, data/, screenshots/, logs/, browser_profile/.
- Sensitive answers (work authorization, EEO, legal attestations) come only from profile.yaml via
  answers.py rules; never route them to the LLM.
