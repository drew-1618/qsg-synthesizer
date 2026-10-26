# QSG Synthesizer

A FastAPI service that ingests two telecom hardware Quick Start Guides (QSGs) or
spec sheets — PDF or plain text/Markdown — and produces a comparative
usability battlecard: an Installation Friction Index (1-10), a normalized
side-by-side spec comparison (install details, Wi-Fi standard, WAN speed,
form factor, port counts, and hardware capabilities), field-deployment
risks, competitive advantages, and a competitive sales talk track. Every
synthesized battlecard is saved to a browsable history and can be exported
as Markdown or PDF.

## Run it yourself

Everything runs on your own computer: there's no account, and your documents
and history stay on your machine.

**Before you start**, install one of these (either works; you don't need both):

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) for
  Windows, macOS or Linux. Larger companies need a paid Docker subscription, so
  if yours doesn't have one, use uv instead.
- [uv](https://docs.astral.sh/uv/getting-started/installation/), a small Python
  tool installer. It downloads a suitable Python by itself, so you don't need
  Python installed first. (pipx works too, with Python 3.11 or newer.)

**With Docker**

```bash
docker run -d --name qsg-synthesizer --restart unless-stopped \
  -p 127.0.0.1:8000:8000 -v qsg-history:/app/data/history \
  ghcr.io/drew-1618/qsg-synthesizer:latest
```

Then open <http://localhost:8000>. It starts again whenever Docker Desktop does,
and history is kept in the `qsg-history` volume.

**Without Docker**

```bash
uv tool install git+https://github.com/drew-1618/qsg-synthesizer
qsg-synthesizer
```

(or `pipx install git+https://github.com/drew-1618/qsg-synthesizer`). The
command opens the dashboard in your browser; `qsg-synthesizer --help` lists the
port and other options. History is saved in your user data folder
(`~/.local/share/qsg-synthesizer` on Linux, `~/Library/Application Support/qsg-synthesizer`
on macOS, `%LOCALAPPDATA%\qsg-synthesizer` on Windows), and the command prints
the exact path at startup. To update, run the install command again with
`--reinstall` (`uv`) or `--force` (`pipx`).

To have it start whenever you log in, run this once (`off` undoes it, `status`
checks it):

```bash
qsg-synthesizer autostart on
```

It uses the `.env` in the folder you run it from, if there is one, and writes
its output to a `qsg-synthesizer.log` file in the data folder above.

**Open it like an app**

In Chrome or Edge, use the install icon in the address bar (or **Settings >
Install as an app**) to get QSG Synthesizer in its own window, with an icon in
your Start menu, Dock or app launcher. In Safari on macOS, use **File > Add to
Dock**. Firefox can't install apps, so bookmark it instead. The app needs QSG
Synthesizer running; with Docker's `--restart` or `autostart on` above, it
always is. If it isn't, the window says how to start it and opens the
dashboard by itself once it's up.

Always use the same address, <http://localhost:8000>. Browsers treat
`localhost` and `127.0.0.1` (or another port) as different sites, each with its
own settings, saved keys and installed app.

**Reading documents with AI (optional)**

Out of the box, documents are read by a built-in parser and nothing leaves your
computer. It reads the common layouts of quick start guides and datasheets, and
says so when it recognized too little of a document to compare it with
confidence. For a fuller reading of any document, open **Settings** in the
dashboard, pick Anthropic or Google Gemini, paste your API key and choose a
model. The key stays in your browser and is sent only to
that provider, along with the text of the documents you compare.

To set keys once for every browser instead, put them in a `.env` file (see
[`.env.example`](.env.example)): in the folder you run `qsg-synthesizer` from,
or pass it to Docker with `--env-file .env`.

Licensed under the [MIT License](LICENSE).

## Architecture

1. **Extraction** (one document in, structured facts out) — delegated to the
   bounded `qsg_extractor` subagent (`.claude/agents/qsg_extractor.md`) when
   an `ANTHROPIC_API_KEY` and/or `GEMINI_API_KEY` is set, so raw multi-page
   documents are never dumped into the orchestrator's own prompt. Anthropic
   (Haiku) is used if its key is present, Gemini (`gemini-2.5-flash-lite`,
   or `GEMINI_MODEL`)
   otherwise — see `LLM_PROVIDER` below to force one explicitly. Without a
   usable key (or if the API call fails), a deterministic regex/heuristic
   parser produces the same shape, so the app and test suite never fail
   hard on missing credentials.
2. **Normalization** — IP classification (`qsg_synthesizer/tools/net_validator.py`)
   and LED-state normalization (`qsg_synthesizer/tools/led_normalizer.py`) are always
   deterministic, never LLM-guessed.
3. **Synthesis** — two normalized `DeviceSpec`s are compared into a
   `BattlecardResponse` via a deterministic scoring/rules engine
   (`qsg_synthesizer/services/orchestrator.py`).

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
- `GEMINI_MODEL` (optional — the Gemini model to extract with; defaults to
  `gemini-2.5-flash-lite`, which has the most free-tier quota. Use e.g.
  `gemini-2.5-flash` for better extraction at a lower request limit.)
- `APP_USERNAME` / `APP_PASSWORD` (optional — set both to gate the dashboard
  and API behind HTTP Basic Auth; `/healthz` always stays open for health
  checks. Neither is set by default, so local dev and the test suite run
  open.)
- `FORWARDED_ALLOW_IPS` (optional — read by uvicorn itself. Behind a reverse
  proxy, set it to the proxy's IP so the per-client rate limits see real client
  addresses instead of counting every request against the proxy. Defaults to
  `127.0.0.1`.)
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
.venv/bin/uvicorn qsg_synthesizer.main:app --reload
```

Open `http://localhost:8000/` for the dashboard, or `http://localhost:8000/docs`
for the OpenAPI docs.

## Frontend build (Tailwind CSS)

The dashboard's styling is compiled ahead of time with the Tailwind CLI rather
than loaded from `cdn.tailwindcss.com` (that CDN build is JIT-compiled in the
browser on every page load and Tailwind's own docs say not to use it in
production). The compiled, minified `qsg_synthesizer/static/tailwind.css` is committed to
the repo, so **no Node/npm install is needed just to run the app**. Only
regenerate it after changing Tailwind classes in `qsg_synthesizer/static/index.html` or
`qsg_synthesizer/static/app.js`:

```bash
npm install
npm run build:css
```

Source input is `qsg_synthesizer/static/src/input.css`; `package.json` pins the Tailwind
CLI version used to generate the committed output.

## Running with Docker

```bash
cp .env.example .env   # optional - only for keys/auth; Settings also takes API keys
docker compose up --build
```

Serves the same app at `http://localhost:8000/`. The port is bound to
localhost only, since auth is off by default; to reach it from other machines,
set `APP_USERNAME`/`APP_PASSWORD` and change the mapping in
`docker-compose.yml` to `"8000:8000"`. Comparison history is kept in the
`qsg-history` named volume, so it survives `docker compose down` and rebuilds
(`docker compose down -v` deletes it).

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

Both `compare-*` endpoints return a `BattlecardResponse` (see `qsg_synthesizer/schemas.py`), save it to
history, and are rate-limited to 10 requests/minute per IP (`/export.*` is limited to
20/minute); exceeding the limit returns `429` with a `Retry-After` header.

## Comparison history

Each `compare-*` call is persisted as its own JSON file under `data/history/` (git-ignored,
created on first use) — no database. Records are kept newest-first and auto-pruned beyond the
50 most recent (`MAX_HISTORY_ENTRIES` in `qsg_synthesizer/services/history.py`). The dashboard's "Recent
Comparisons" panel lists, reloads, deletes, and exports from this store.

## Logging

Configured once at startup (`qsg_synthesizer/logging_config.py`) to stdout as one JSON object per line
(`{"timestamp", "level", "logger", "message"}`, plus `"exception"` when a traceback is
attached), level controlled by `LOG_LEVEL` (default `INFO`) — easy to pipe into a log
aggregator without a reformatting step. Uvicorn's own access log is unaffected and stays in
its normal plain-text form. Beyond that access log, the app logs:

- a rejected Basic Auth attempt (client IP, and username if one was sent — never the password)
- a request that hit the rate limiter (client IP, limit)
- a failed LLM extraction call before falling back to the heuristic parser (with traceback)
- any other unhandled exception (with traceback), while the client still only ever sees a
  generic `{"detail": "Internal server error"}` — no internals leak into the response
