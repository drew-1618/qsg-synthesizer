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
`POST /api/v1/compare` (the UI's endpoint; each side is an upload or pasted text), or the older
`/compare-pdfs` / `/compare-text`, in `app/main.py`:
1. `app/tools/document_parser.py` turns a PDF, `.docx`, Markdown or text upload into Markdown
   (PDFs via `pdf_parser.py` / pymupdf4llm; `.docx` is walked in body order with a zip-bomb guard)
   and every format goes through `text_cleaning.clean_text()` to strip boilerplate.
2. `orchestrator.extract_device_spec()` runs once per device:
   - `extract_qsg_fields` produces a raw `QSGExtraction` (`app/models/schemas.py`) via an LLM
     (Anthropic or Gemini, chosen by `_select_llm_provider` / `LLM_PROVIDER`), using
     `.claude/agents/qsg_extractor.md` (frontmatter stripped) as the system prompt. With no
     usable key, or on *any* API/parse error, it silently falls back to `_heuristic_extract_qsg`,
     a regex parser keyed on the heading names used in `sample_docs/*.md`.
   - `_normalize_to_device_spec` converts it into the API-facing `DeviceSpec` (`app/schemas.py`)
     using the deterministic tools in `app/tools/`.
3. `synthesize_battlecard()` is fully rule-based (friction score, risks, advantages, talk track).
4. `app/services/history.py` saves the result as `data/history/<uuid hex>.json` (capped at 50);
   the export endpoints (`app/services/export.py`) render from those saved records.

Things that span multiple files:
- Export file names: `export.default_export_basename()` builds `battlecard-<a>-vs-<b>-<date>`
  and `export.sanitize_export_filename()` cleans any user-supplied `?filename=` to ASCII
  `[A-Za-z0-9._()-]` before it reaches `Content-Disposition`. The UI downloads via `fetch` so
  errors show inline in `#export-dialog`; keep new export formats going through both helpers.
- Two schema layers with two provisioning enums: the raw 6-value `RawProvisioningMethod` in
  `app/models/schemas.py` and the 4-value API `ProvisioningMethod` in `app/schemas.py`.
- `.claude/agents/*.md` are read at runtime, so the Dockerfile copies `.claude/`, and
  `.dockerignore` must not exclude `*.md` there. `sample_docs/*.pdf` likewise ship in the
  image for `GET /api/v1/samples`, and the tests read `sample_docs/*.md|pdf`.
- `NO_RISKS_MESSAGE` / `NO_ADVANTAGES_MESSAGE` in `orchestrator.py` are matched by exact
  string in `app/static/app.js`; change both together.
- Auth (`app/services/auth.py`) is optional Basic Auth via `APP_USERNAME`/`APP_PASSWORD`.
  It applies through the `router` dependency in `main.py`, plus a separate middleware for
  `/static/*` and the docs routes `/docs`, `/redoc`, `/openapi.json` (registered on `app`, outside the router). `/healthz` is
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

## Design Direction
Concept: network-equipment spec plate / rack panel. Status LEDs are the one memorable element;
everything else stays quiet. Sentence case, no all-caps labels, no tinted-card-for-everything.
- Fonts (self-hosted in `app/static/fonts/`, declared in `app/static/src/input.css`): Archivo
  (headings, `font-heading`) + IBM Plex Sans (body) + JetBrains Mono only for IPs and port names.
  Not Inter. To update, reinstall from `@fontsource-variable/*` and copy the woff2 files.
- Palette (Tailwind `@theme` tokens in `input.css`): `panel` #eef0ee, `plate` #f9faf8, `ink`
  #1c2127, `ink-muted` #56616b, `line` #c9d0cb, `bezel` #2b333b, `action` #0d6b6b, `danger` #b3261e (destructive
  actions, error borders), plus
  `led-green/amber/red/blue` for status only. Use the tokens, never raw hex in markup or JS.
- Themes: light and dark share the same token names. Dark overrides them in
  `:root[data-theme="dark"]` in `input.css`; never branch on theme in markup, use tokens (e.g.
  `bg-plate-hover`, `text-on-action`, `bg-scrim`, not `bg-white`/`text-white`). `data-theme` is set
  before first paint by the inline script in `index.html` (stored `qsg-theme`, else the OS
  setting) and toggled by `applyTheme()` in `app.js`. Check contrast per theme when adding tokens.
- Spacing: 4px base scale (Tailwind default at a 16px root); avoid arbitrary values.
- Mobile-first, test at 375px width.
- No `src/components/` dir here (vanilla JS, no build step beyond Tailwind). Build reusable UI as
  small render helpers in `app/static/app.js` / shared markup in `index.html`, and avoid one-off
  inline `style=` attributes. Allowed exception: values computed at runtime (the
  LED animation duration).
- Rebuild CSS with `npm run build:css` after changing classes or theme tokens.
- Dialogs are native `<dialog>` elements (history drawer, `confirmDialog()`), so focus trap, Esc
  and top-layer stacking come from the browser. Error text should say what happened and what to do
  next (`describeFailure()` in `app.js`); the open comparison is mirrored in the URL as `?id=`.
