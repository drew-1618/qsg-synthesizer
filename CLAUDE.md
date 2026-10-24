# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture Overview
A containerized FastAPI service running a multi-agent pipeline that ingests telecom hardware Quick Start Guides (QSGs) or spec sheets, isolates extraction into bounded subagents, normalizes metrics via deterministic tools, and produces a comparative usability battlecard.

## Commands
```bash
.venv/bin/uvicorn app.main:app --reload          # dashboard at /, OpenAPI at /docs
.venv/bin/pytest -q                              # full suite (offline, no API keys needed)
.venv/bin/pytest -q tests/test_api.py::TestHistory::test_delete_removes_item_from_list  # single test
.venv/bin/ruff check . && .venv/bin/black --check . && .venv/bin/mypy   # same checks as CI
.venv/bin/pip-audit -r requirements.txt -r requirements-dev.txt
npm install && npm run build:css                 # only after changing Tailwind classes in index.html/app.js
python sample_docs/generate_samples.py           # regenerate the sample QSG PDFs
docker compose up --build                        # requires a .env file (cp .env.example .env)
```

## Repository Standards
- Python: 3.11+ (CI and the Docker image use 3.11)
- Framework: FastAPI + Uvicorn
- Testing: pytest -q (Always run tests quietly to save context tokens)
- Formatting/typing: Black / Ruff / mypy, all enforced in CI and via `pre-commit install`
  (`.pre-commit-config.yaml`) — run `pip install -r requirements.txt -r requirements-dev.txt`
  once to get all three locally. Mypy needs the runtime requirements installed too.
- Dependencies: `pip-audit -r requirements.txt -r requirements-dev.txt` runs in CI; keep new
  or bumped pins audit-clean.
- Logging: use `logging.getLogger(__name__)` per module (configured centrally in
  `app/logging_config.py`, level via `LOG_LEVEL`). Never log credentials — e.g. auth failures
  log the attempted username, never the password.

## Pipeline (how a request flows)
`POST /api/v1/compare-pdfs` (or `/compare-text`) in `app/main.py`:
1. `app/tools/pdf_parser.py` turns PDF bytes into Markdown via pymupdf4llm and strips boilerplate.
2. `orchestrator.extract_device_spec()` runs once per device:
   - `extract_qsg_fields` produces a raw `QSGExtraction` (`app/models/schemas.py`) via an LLM
     (Anthropic or Gemini, chosen by `_select_llm_provider` / `LLM_PROVIDER`), using
     `.claude/agents/qsg_extractor.md` (frontmatter stripped) as the system prompt. With no
     usable key, or on *any* API/parse error, it silently falls back to `_heuristic_extract_qsg`,
     a regex parser keyed on the heading names used in `sample_docs/*.md`.
   - `_normalize_to_device_spec` converts it into the API-facing `DeviceSpec` (`app/schemas.py`)
     using the deterministic tools in `app/tools/`.
3. `synthesize_battlecard()` is fully rule-based (friction score, risks, advantages, talk track).
   `.claude/agents/battlecard_analyst.md` is not used at runtime.
4. `app/services/history.py` saves the result as `data/history/<uuid hex>.json` (capped at 50);
   the export endpoints (`app/services/export.py`) render from those saved records.

Things that span multiple files:
- Two schema layers with a same-named `ProvisioningMethod` enum: the raw 6-value one in
  `app/models/schemas.py` and the 4-value API one in `app/schemas.py`. The orchestrator
  imports them as `RawProvisioningMethod` / `ApiProvisioningMethod`.
- `.claude/agents/*.md` are read at runtime, so the Dockerfile copies `.claude/`, and
  `.dockerignore` must not exclude `*.md` there. `sample_docs/*.pdf` likewise ship in the
  image for `GET /api/v1/samples`, and the tests read `sample_docs/*.md|pdf`.
- `NO_RISKS_MESSAGE` / `NO_ADVANTAGES_MESSAGE` in `orchestrator.py` are matched by exact
  string in `app/static/app.js`; change both together.
- Auth (`app/services/auth.py`) is optional Basic Auth via `APP_USERNAME`/`APP_PASSWORD`.
  It applies through the `router` dependency in `main.py`, plus a separate middleware for
  `/static/*` (StaticFiles is mounted on `app`, outside the router). `/healthz` is
  deliberately left open for the Docker/CI health check.
- `main.py` calls `load_dotenv()` before importing `app.*` because `HISTORY_DIR` is read at
  import time. It skips this under pytest (`PYTEST_VERSION`) so tests never pick up real API
  keys from `.env`.
- `app/static/tailwind.css` is compiled and committed. No Node is needed to run the app, but
  new Tailwind classes have no styles until `npm run build:css` is rerun.

## Testing Notes
- `tests/conftest.py` autouse fixtures redirect `history.HISTORY_DIR` to `tmp_path` and reset
  both rate limiters, so tests never touch `data/` and don't depend on order.
- LLM paths are tested with mocks by patching `app.services.orchestrator.anthropic` /
  `app.services.orchestrator.genai` and setting the key env vars with `patch.dict`.
- Most API tests run end-to-end through the heuristic parser on `sample_docs/`, so changing
  those sample docs or the heuristic parser can shift the expected friction scores.

## Agent Principles
- Never dump raw multi-page documents into the primary orchestrator prompt.
- Always delegate document parsing to isolated subagents in `.claude/agents/`.
- Use deterministic tools in `app/tools/` for IP validation and unit conversion rather than LLM guessing.

## UI Conventions
- Never use native `window.confirm()`/`alert()` in `app/static/app.js`. Use the `confirmDialog()`
  helper (custom modal in `index.html`) so confirmations match the app's own styling.
