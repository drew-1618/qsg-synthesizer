# QSG Synthesizer - Project Instructions

## Architecture Overview
A containerized FastAPI service running a multi-agent pipeline that ingests telecom hardware Quick Start Guides (QSGs) or spec sheets, isolates extraction into bounded subagents, normalizes metrics via deterministic tools, and produces a comparative usability battlecard.

## Repository Standards
- Python: 3.11+
- Framework: FastAPI + Uvicorn
- Testing: pytest -q (Always run tests quietly to save context tokens)
- Formatting/typing: Black / Ruff / mypy, all enforced in CI and via `pre-commit install`
  (`.pre-commit-config.yaml`) — run `pip install -r requirements.txt -r requirements-dev.txt`
  once to get all three locally.
- Dependencies: `pip-audit -r requirements.txt -r requirements-dev.txt` runs in CI; keep new
  or bumped pins audit-clean.
- Logging: use `logging.getLogger(__name__)` per module (configured centrally in
  `app/logging_config.py`, level via `LOG_LEVEL`). Never log credentials — e.g. auth failures
  log the attempted username, never the password.

## Agent Principles
- Never dump raw multi-page documents into the primary orchestrator prompt.
- Always delegate document parsing to isolated subagents in `.claude/agents/`.
- Use deterministic tools in `app/tools/` for IP validation and unit conversion rather than LLM guessing.

## UI Conventions
- Never use native `window.confirm()`/`alert()` in `app/static/app.js`. Use the `confirmDialog()`
  helper (custom modal in `index.html`) so confirmations match the app's own styling.
