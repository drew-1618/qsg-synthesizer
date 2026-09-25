# QSG Synthesizer

A FastAPI service that ingests two telecom hardware Quick Start Guides (QSGs) or
spec sheets — PDF or plain text/Markdown — and produces a comparative
usability battlecard: an Installation Friction Index (1-10), a normalized
side-by-side spec comparison, field-deployment risks, and a competitive sales
talk track.

## Architecture

1. **Extraction** (one document in, structured facts out) — delegated to the
   bounded `qsg_extractor` subagent (`.claude/agents/qsg_extractor.md`, Haiku)
   when `ANTHROPIC_API_KEY` is set, so raw multi-page documents are never
   dumped into the orchestrator's own prompt. Without a key (or if the API
   call fails), a deterministic regex/heuristic parser produces the same
   shape, so the app and test suite never fail hard on missing credentials.
2. **Normalization** — IP validation/classification (`app/tools/net_validator.py`)
   and LED-state normalization (`app/tools/led_normalizer.py`) are always
   deterministic, never LLM-guessed.
3. **Synthesis** — two normalized `DeviceSpec`s are compared into a
   `BattlecardResponse` via a deterministic scoring/rules engine
   (`app/services/orchestrator.py`).

## Requirements

- Python 3.11+
- An `ANTHROPIC_API_KEY` (optional — enables the real `qsg_extractor` LLM
  call; the app runs fully without one via the heuristic fallback)

## Running locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # optionally set ANTHROPIC_API_KEY
.venv/bin/uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/` for the dashboard, or `http://127.0.0.1:8000/docs`
for the OpenAPI docs.

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

## API

- `GET /` — dashboard (drag-and-drop two QSGs, view the battlecard)
- `GET /healthz` — health check
- `GET /api/v1/samples` — base64-encoded sample PDFs, for the dashboard's demo button
- `POST /api/v1/compare-text` — JSON body `{device_a_text, device_a_name, device_b_text, device_b_name}`
- `POST /api/v1/compare-pdfs` — multipart upload, two PDF files (`device_a_pdf`, `device_b_pdf`)

Both `compare-*` endpoints return a `BattlecardResponse` (see `app/schemas.py`).
