# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture Overview
A containerized FastAPI service running a multi-agent pipeline that ingests telecom hardware Quick Start Guides (QSGs) or spec sheets, isolates extraction into bounded subagents, normalizes metrics via deterministic tools, and produces a comparative usability battlecard.

## Commands
```bash
.venv/bin/uvicorn qsg_synthesizer.main:app --reload          # dashboard at /, OpenAPI at /docs
.venv/bin/pytest -q                              # full suite (offline, no API keys needed)
.venv/bin/pytest -q tests/test_api.py::TestHistory::test_delete_removes_item_from_list  # single test
.venv/bin/ruff check . && .venv/bin/black --check . && .venv/bin/mypy   # same checks as CI
.venv/bin/pip-audit -r requirements.txt -r requirements-dev.txt
npm install && npm run build:css                 # only after changing Tailwind classes in index.html/app.js
python sample_docs/generate_samples.py           # regenerate the sample QSG PDFs
docker compose up --build                        # .env optional (cp .env.example .env for server keys)
uv tool install . && qsg-synthesizer             # installed-package path (qsg_synthesizer/cli.py), as users run it
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
  `qsg_synthesizer/logging_config.py`, level via `LOG_LEVEL`). Never log credentials — e.g. auth failures
  log the attempted username, never the password.

## Pipeline (how a request flows)
`POST /api/v1/compare` (the UI's endpoint; each side is an upload or pasted text), or the older
`/compare-pdfs` / `/compare-text`, in `qsg_synthesizer/main.py`:
1. `qsg_synthesizer/tools/document_parser.py` turns a PDF, `.docx`, Markdown or text upload into Markdown
   (PDFs via `pdf_parser.py` / pymupdf4llm; `.docx` is walked in body order with a zip-bomb guard)
   and every format goes through `text_cleaning.clean_text()` to strip boilerplate.
2. `orchestrator.extract_device_spec()` runs once per device:
   - `extract_qsg_fields` produces a raw `QSGExtraction` (`qsg_synthesizer/models/schemas.py`) via an LLM
     (Anthropic or Gemini, chosen by `_select_llm_provider` / `LLM_PROVIDER`), using
     `.claude/agents/qsg_extractor.md` (frontmatter stripped) as the system prompt. With no
     usable key, or on *any* API/parse error, it falls back to `_heuristic_extract_qsg`,
     a regex parser: heading wordings map to sections via `_SECTION_PATTERNS`, tables are read
     by their header row (`_parse_tables`), and `_heuristic_extract` also returns warnings when
     it read only the first model of a multi-model spec table or recognized too little
     (`THIN_INSTALL_WARNING` / `THIN_SPECS_WARNING`).
   - The dashboard's Settings can override the provider per request: `POST /api/v1/compare`
     reads `X-LLM-Provider` (`anthropic`/`gemini`/`heuristic`), `X-LLM-API-Key` and
     `X-LLM-Model` into an `LLMOverride`, which `_resolve_llm` turns into (provider, key) and
     which is passed explicitly down to the extractors (not via contextvars, because of the
     thread pools). `X-LLM-Model` must be on that provider's `LLM_MODEL_CHOICES` list in
     `orchestrator.py`; without it the provider's `default_model()` is used (`GEMINI_MODEL`
     for Gemini). User keys are never stored or logged server-side. Any failed LLM call (user
     or server key) adds a user-facing warning via `_fallback_warning` instead of falling back
     silently. `GET /api/v1/settings` reports the server's own provider and each provider's
     model list and default, which the Settings model picker is built from.
   - `_normalize_to_device_spec` converts it into the API-facing `DeviceSpec` (`qsg_synthesizer/schemas.py`)
     using the deterministic tools in `qsg_synthesizer/tools/`.
   - Documents longer than `MAX_LLM_INPUT_CHARS` are not truncated: `_extract_document` splits
     them on Markdown headings (`_split_into_chunks`, at most `_MAX_CHUNKS` parts), runs the same
     bounded extractor per part, and `_merge_extractions` combines them deterministically. Skipped
     parts become `DeviceSpec.extraction_warnings` and then `BattlecardResponse.warnings`.
   - Each extraction carries a `document_type` (`quick_start_guide`/`datasheet`/`other`; the
     heuristic only says datasheet on clear evidence) and open-ended `specs` rows.
3. `synthesize_battlecard()` is fully rule-based (friction score, risks, advantages, talk track).
   Friction is scored only when both documents are quick start guides (`_is_install_document`,
   which treats a missing type as a guide) and both list a provisioning method; otherwise both
   scores are `None` and the talk track is spec-based. For two guides `_unscored_reason` says
   which one doesn't document provisioning, as `BattlecardResponse.unscored_reason`, which the
   UI verdict, talk track and exports show. Console/static-IP risks likewise need a documented
   method, since the `Console` primary method is otherwise only a fallback. The UI, history
   and exports all handle `None` scores.
4. `qsg_synthesizer/services/history.py` saves the result as `data/history/<uuid hex>.json` (capped at 50);
   the export endpoints (`qsg_synthesizer/services/export.py`) render from those saved records. Each record
   stores `input_fingerprint` (a sha256 of both input texts). The drawer loads
   `GET /api/v1/history/stacks`, which groups records by device pair and mode (`_stack_key`) and
   marks older runs of identical documents as `redundant_ids`, which
   `DELETE /api/v1/history/{id}/copies` removes. The two input texts are kept as
   `data/history/sources/<sha256>.txt` (one file per distinct text, listed in
   `HistoryRecord.sources`; deleting a record removes the texts only it used at once, and
any other unreferenced text goes after a short grace period) so
   `POST /api/v1/history/{id}/rerun` can read them again with the request's `X-LLM-*` settings
   and the record's mode; records without sources get a 409. Keeping them is on by default
   and switchable per browser in Settings (`qsg-keep-documents`, sent as the `keep_documents`
   form field of `POST /api/v1/compare`); `DELETE /api/v1/history/documents`
   (`forget_all_sources`) strips them from every record but keeps the battlecards.
   In the UI, a result on screen replaces the form with `#result-bar` (`showResultBar()`):
   Rerun, Change documents (only after a fresh compare, when the form still holds the files)
   and New comparison.

Things that span multiple files:
- Comparison perspective: `BattlecardResponse.perspective` is `"sales"` (side A is the user's own
  device, B the competitor; the talk track in `_build_sales_talk_track` flips with who wins on
  install effort), `"neutral"`, or `None` for older records and the legacy endpoints (treated as
  neutral). Only `POST /api/v1/compare` takes `mode` (default `sales`); the UI labels
  ("Our device"/"Competitor", role tags in results, history wording) all derive from it.
- Export file names: `export.default_export_basename()` builds `battlecard-<a>-vs-<b>-<date>`
  and `export.sanitize_export_filename()` cleans any user-supplied `?filename=` to ASCII
  `[A-Za-z0-9._()-]` before it reaches `Content-Disposition`. The UI downloads via `fetch` so
  errors show inline in `#export-dialog`; keep new export formats going through both helpers.
- Two schema layers with two provisioning enums: the raw 6-value `RawProvisioningMethod` in
  `qsg_synthesizer/models/schemas.py` and the 4-value API `ProvisioningMethod` in `qsg_synthesizer/schemas.py`.
- Paths go through `qsg_synthesizer/paths.py`: the repo's `.claude/agents` and `sample_docs` when present
  (checkout, Docker), else copies the wheel bundles into `qsg_synthesizer/_bundled/` (`force-include` in
  `pyproject.toml`). History defaults to `<repo>/data/history` in a checkout, the per-user data
  folder for an installed package, and the Dockerfile sets `HISTORY_DIR`. `pyproject.toml`
  dependencies must match `requirements.txt` (enforced by `tests/test_packaging.py`).
- Installed app (PWA): `/manifest.webmanifest` and `/sw.js` are root routes in `main.py` (a
  worker only controls pages under its own path). `sw.js` caches nothing but `offline.html`
  and the assets it lists in `OFFLINE_ASSETS` (`tests/test_pwa.py` checks they exist and that
  `offline.html` loads nothing else); bump its `CACHE` name when that list changes. `/` and
  `/static/*` send `Cache-Control: no-cache` so a stopped server shows the offline page.
- The launcher (`cli.py`) opens `BROWSER_HOST` (`localhost`), matching the README's Docker
  instructions: localhost and 127.0.0.1 are different sites with separate settings, keys and
  installed app. `autostart.py` writes per-OS login entries (systemd user unit, launchd
  agent, HKCU Run key) that run `python -m qsg_synthesizer --no-browser --log-file …`.
- `.claude/agents/*.md` are read at runtime, so the Dockerfile copies `.claude/`, and
  `.dockerignore` must not exclude `*.md` there. `sample_docs/*.pdf` likewise ship in the
  image for `GET /api/v1/samples`, and the tests read `sample_docs/*.md|pdf`.
- `NO_RISKS_MESSAGE` / `NO_ADVANTAGES_MESSAGE` in `orchestrator.py` are matched by exact
  string in `qsg_synthesizer/static/app.js`; change both together.
- Auth (`qsg_synthesizer/services/auth.py`) is optional Basic Auth via `APP_USERNAME`/`APP_PASSWORD`.
  It applies through the `router` dependency in `main.py`, plus a separate middleware for
  `/static/*` and the docs routes `/docs`, `/redoc`, `/openapi.json` (registered on `app`, outside the router). `/healthz` is
  deliberately left open for the Docker/CI health check.
- `main.py` loads `./.env` (current folder only, never a parent's) before importing `qsg_synthesizer.*`
  because `HISTORY_DIR` is read at import time. It skips this under pytest (`PYTEST_VERSION`) so
  tests never pick up real API keys from `.env`, and when the `qsg-synthesizer` command already
  loaded one (`QSG_ENV_LOADED`; `--env-file`/`--no-env-file` in `cli.py`).
- `qsg_synthesizer/static/tailwind.css` is compiled and committed. No Node is needed to run the app, but
  new Tailwind classes have no styles until `npm run build:css` is rerun.

## Testing Notes
- `tests/conftest.py` autouse fixtures redirect `history.HISTORY_DIR` to `tmp_path` and reset
  both rate limiters, so tests never touch `data/` and don't depend on order.
- LLM paths are tested with mocks by patching `qsg_synthesizer.services.orchestrator.anthropic` /
  `qsg_synthesizer.services.orchestrator.genai` and setting the key env vars with `patch.dict`.
- Most API tests run end-to-end through the heuristic parser on `sample_docs/`, so changing
  those sample docs or the heuristic parser can shift the expected friction scores.
  `tests/test_realistic_docs.py` pins what the parser extracts from each sample doc (text and
  PDF) and tests it against `tests/fixtures/realistic/`, vendor-style guides and datasheets
  for fictional devices laid out unlike the samples; add a fixture there for a new layout.
- `sample_docs/device_c_datasheet.md` (bullet specs) and `device_d_datasheet.md` (table specs)
  are the datasheet fixtures; `tests/test_datasheets.py` covers document types, spec rows,
  unscored comparisons and long-document chunking with a fake LLM.
- Running the app or a script outside pytest loads `.env` and makes real LLM calls. For manual
  checks start uvicorn with `GEMINI_API_KEY= ANTHROPIC_API_KEY= LLM_PROVIDER=` (empty values win
  over `.env`) unless you mean to use a real provider.

## Agent Principles
- Never dump raw multi-page documents into the primary orchestrator prompt.
- Always delegate document parsing to isolated subagents in `.claude/agents/`.
- Use deterministic tools in `qsg_synthesizer/tools/` for IP validation and unit conversion rather than LLM guessing.

## UI Conventions
- Never use native `window.confirm()`/`alert()` in `qsg_synthesizer/static/app.js`. Use the `confirmDialog()`
  helper (custom modal in `index.html`) so confirmations match the app's own styling.

## Design Direction
Concept: network-equipment spec plate / rack panel. Status LEDs are the one memorable element;
everything else stays quiet. Sentence case, no all-caps labels, no tinted-card-for-everything.
- Fonts (self-hosted in `qsg_synthesizer/static/fonts/`, declared in `qsg_synthesizer/static/src/input.css`): Archivo
  (headings, `font-heading`) + IBM Plex Sans (body) + JetBrains Mono only for IPs and port names.
  Not Inter. To update, reinstall from `@fontsource-variable/*` and copy the woff2 files.
- Palette (Tailwind `@theme` tokens in `input.css`): `panel` #eef0ee, `plate` #f9faf8, `ink`
  #1c2127, `ink-muted` #56616b, `line` #c9d0cb, `bezel` #2b333b, `action` #0d6b6b, `danger` #b3261e (destructive
  actions, error borders; text on it uses `on-danger`), plus
  `led-green/amber/red/blue` for status only. Use the tokens, never raw hex in markup or JS.
- Themes: light and dark share the same token names. Dark overrides them in
  `:root[data-theme="dark"]` in `input.css`; never branch on theme in markup, use tokens (e.g.
  `bg-plate-hover`, `text-on-action`, `bg-scrim`, not `bg-white`/`text-white`). `data-theme` is set
  before first paint by the inline script in `index.html` (stored `qsg-theme`, else the OS
  setting) and changed from the Settings dialog (`#settings-dialog`, Light/Dark/System) via
  `setThemePreference()` in `app.js`; "System" removes `qsg-theme`. Check contrast per theme
  when adding tokens.
- Spacing: 4px base scale (Tailwind default at a 16px root); avoid arbitrary values.
- Mobile-first, test at 375px width.
- No `src/components/` dir here (vanilla JS, no build step beyond Tailwind). Build reusable UI as
  small render helpers in `qsg_synthesizer/static/app.js` / shared markup in `index.html`, and avoid one-off
  inline `style=` attributes. Allowed exception: values computed at runtime (the
  LED animation duration).
- Rebuild CSS with `npm run build:css` after changing classes or theme tokens.
- Dialogs are native `<dialog>` elements (history drawer, `confirmDialog()`), so focus trap, Esc
  and top-layer stacking come from the browser. The drawer's slide (`.drawer-slide` in `input.css`) is pure CSS
  (`@starting-style` + `allow-discrete`) and respects reduced motion; no JS waits on it. Error text should say what happened and what to do
  next (`describeFailure()` in `app.js`); the open comparison is mirrored in the URL as `?id=`.
