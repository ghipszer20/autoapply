# autoapply

Personal pipeline that finds Summer 2027 / off-cycle internships (SWE, data science, quant dev, AI/ML)
and applies automatically on each company's own application system.

Design: `docs/superpowers/specs/2026-09-29-autoapply-design.md`

## Setup
    py -3.13 -m venv .venv
    .venv/Scripts/python -m pip install -e ".[dev]"
    # review filter settings in config.yaml

## Use
    .venv/Scripts/autoapply discover   # fetch + dedupe + filter into autoapply.db
    .venv/Scripts/autoapply status
    .venv/Scripts/autoapply on|off     # kill switch (new installs start off)

## Phone control
Start `claude --remote-control` in this folder, then enable push notifications in `/config`.
From the Claude app, say "on", "off" or "status". See CLAUDE.md.
