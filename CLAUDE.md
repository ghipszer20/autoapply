# autoapply — operating instructions for Claude Code sessions

This repo runs an automatic internship-application pipeline. The user usually talks to this
session from the Claude mobile app through Remote Control. Keep replies to one or two short lines.

## Phone commands
Run commands from the repo root with `.venv/Scripts/autoapply`:
- "on" / "start" / "resume": run `.venv/Scripts/autoapply on`, then `.venv/Scripts/autoapply status`
- "off" / "stop" / "pause": run `.venv/Scripts/autoapply off`, then `.venv/Scripts/autoapply status`
- "status" / "how's it going": run `.venv/Scripts/autoapply status` and summarize in one line

Never edit code, config, or git state in response to a phone command. If a request is anything other
than on/off/status, say so and ask the user to do it from the PC.

## Development rules
- Own git repo at this folder; the parent home dir is a separate repo with secrets. Check
  `git rev-parse --show-toplevel` before staging and review `git diff --cached --name-only`.
- Private GitHub repo; no GitHub Actions.
- Tests: `.venv/Scripts/python -m pytest -q` (network-free); live checks: `-m live`.
