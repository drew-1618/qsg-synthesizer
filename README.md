# QSG Synthesizer

A FastAPI service that ingests two telecom hardware Quick Start Guides (QSGs) or
spec sheets — PDF or plain text/Markdown — and produces a comparative
usability battlecard: an Installation Friction Index (1-10), a normalized
side-by-side spec comparison (install details, Wi-Fi standard, WAN speed,
form factor, port counts, and hardware capabilities), field-deployment
risks, competitive advantages, and a competitive sales talk track. Every
synthesized battlecard is saved to a browsable history and can be exported
as Markdown or PDF.

## Architecture

1. **Extraction** (one document in, structured facts out) — delegated to the
   bounded `qsg_extractor` subagent (`.claude/agents/qsg_extractor.md`) when
   an `ANTHROPIC_API_KEY` and/or `GEMINI_API_KEY` is set, so raw multi-page
   documents are never dumped into the orchestrator's own prompt. Anthropic
   (Haiku) is used if its key is present, Gemini (`gemini-2.5-flash`)
   otherwise — see `LLM_PROVIDER` below to force one explicitly. Without a
   usable key (or if the API call fails), a deterministic regex/heuristic
   parser produces the same shape, so the app and test suite never fail
   hard on missing credentials.
2. **Normalization** — IP validation/classification (`app/tools/net_validator.py`)
   and LED-state normalization (`app/tools/led_normalizer.py`) are always
   deterministic, never LLM-guessed.
3. **Synthesis** — two normalized `DeviceSpec`s are compared into a
   `BattlecardResponse` via a deterministic scoring/rules engine
   (`app/services/orchestrator.py`).

## Requirements

- Python 3.11+
- An `ANTHROPIC_API_KEY` and/or `GEMINI_API_KEY` (both optional — either
  enables the real `qsg_extractor` LLM call; the app runs fully without
  either via the heuristic fallback)
- `LLM_PROVIDER` (optional — `anthropic` or `gemini`; forces that provider
  when both keys above are set. Unset auto-detects: Anthropic wins if both
  keys are present, else whichever one is set. Naming a provider whose
  key/package isn't available falls back to the heuristic parser rather
  than silently using the other provider.)
- `APP_USERNAME` / `APP_PASSWORD` (optional — set both to gate the dashboard
  and API behind HTTP Basic Auth; `/healthz` always stays open for health
  checks. Neither is set by default, so local dev and the test suite run
  open.)
- `LOG_LEVEL` (optional — defaults to `INFO`; set to `DEBUG` for extra detail
  like which extraction path, LLM or heuristic, each request took)
- `HISTORY_DIR` (optional — defaults to `<repo>/data/history`; point a second
  server instance at a scratch directory instead, e.g. `HISTORY_DIR=/tmp/qsg-scratch-history`,
  so ad hoc verification runs or scripts never share/clobber a real dev
  server's on-disk history)

## Running locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # optionally set ANTHROPIC_API_KEY
.venv/bin/uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/` for the dashboard, or `http://127.0.0.1:8000/docs`
for the OpenAPI docs.

## Frontend build (Tailwind CSS)

The dashboard's styling is compiled ahead of time with the Tailwind CLI rather
than loaded from `cdn.tailwindcss.com` (that CDN build is JIT-compiled in the
browser on every page load and Tailwind's own docs say not to use it in
production). The compiled, minified `app/static/tailwind.css` is committed to
the repo, so **no Node/npm install is needed just to run the app**. Only
regenerate it after changing Tailwind classes in `app/static/index.html` or
`app/static/app.js`:

```bash
npm install
npm run build:css
```

Source input is `app/static/src/input.css`; `package.json` pins the Tailwind
CLI version used to generate the committed output.

## Running with Docker

```bash
cp .env.example .env   # required - docker-compose.yml expects this file to exist
docker compose up --build
```

Serves the same app at `http://localhost:8000/`.

## Tests

```bash
.venv/bin/pytest -q
```

Runs fully offline — no `ANTHROPIC_API_KEY` required (the Anthropic-backed
extraction path is covered separately with mocks in `tests/test_orchestrator.py`).

## Lint & format

```bash
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/black --check .
.venv/bin/mypy
```

All three run in CI on every push/PR (see `.github/workflows/test.yml`); `black .` (no
`--check`) applies formatting fixes locally. Mypy needs `requirements.txt` installed too (not
just the dev tools), since it reads type info from the real runtime dependencies.

To catch the same issues before they're committed, install the git hook once (after
`pip install -r requirements.txt -r requirements-dev.txt`):

```bash
.venv/bin/pre-commit install
```

Every future `git commit` then runs `ruff check --fix`, `black`, and `mypy` on the staged
Python files; if a hook modifies a file, re-stage it and commit again. The hooks invoke
`ruff`/`black`/`mypy` from `PATH`, so activate the venv (`source .venv/bin/activate`) before
committing, or they'll report "Executable not found".

## Dependency vulnerability scanning

```bash
.venv/bin/pip-audit -r requirements.txt -r requirements-dev.txt
```

Also runs in CI as its own `security` job, checking every pinned version in both requirements
files against the OSV/PyPA advisory databases.

## API

- `GET /` — dashboard (drag-and-drop two QSGs, view the battlecard, browse/export history)
- `GET /healthz` — health check
- `GET /api/v1/samples` — base64-encoded sample PDFs, for the dashboard's demo button
- `POST /api/v1/compare-text` — JSON body `{device_a_text, device_a_name, device_b_text, device_b_name}`
  (each text field capped at 2,000,000 characters)
- `POST /api/v1/compare-pdfs` — multipart upload, two PDF files (`device_a_pdf`, `device_b_pdf`,
  20MB max each)
- `GET /api/v1/history?limit=&offset=&q=` — paginated list of past comparisons (id, device names,
  timestamp, friction scores); `limit` defaults to 20 (max 50), `offset` defaults to 0. `q`
  filters case-insensitively on either device name. Response is `{items, total, limit, offset}`.
- `GET /api/v1/history/{id}` — full saved battlecard for one comparison
- `GET /api/v1/history/{id}/export.md` / `.../export.pdf` — download a saved battlecard as
  Markdown or PDF
- `DELETE /api/v1/history/{id}` — remove one comparison from history
- `DELETE /api/v1/history` — clear all comparison history

Both `compare-*` endpoints return a `BattlecardResponse` (see `app/schemas.py`), save it to
history, and are rate-limited to 10 requests/minute per IP (`/export.*` is limited to
20/minute); exceeding the limit returns `429` with a `Retry-After` header.

## Comparison history

Each `compare-*` call is persisted as its own JSON file under `data/history/` (git-ignored,
created on first use) — no database. Records are kept newest-first and auto-pruned beyond the
50 most recent (`MAX_HISTORY_ENTRIES` in `app/services/history.py`). The dashboard's "Recent
Comparisons" panel lists, reloads, deletes, and exports from this store.

## Logging

Configured once at startup (`app/logging_config.py`) to stdout as one JSON object per line
(`{"timestamp", "level", "logger", "message"}`, plus `"exception"` when a traceback is
attached), level controlled by `LOG_LEVEL` (default `INFO`) — easy to pipe into a log
aggregator without a reformatting step. Uvicorn's own access log is unaffected and stays in
its normal plain-text form. Beyond that access log, the app logs:

- a rejected Basic Auth attempt (client IP, and username if one was sent — never the password)
- a request that hit the rate limiter (client IP, limit)
- a failed LLM extraction call before falling back to the heuristic parser (with traceback)
- any other unhandled exception (with traceback), while the client still only ever sees a
  generic `{"detail": "Internal server error"}` — no internals leak into the response
