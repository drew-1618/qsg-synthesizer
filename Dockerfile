# syntax=docker/dockerfile:1

# ---- Stage 1: builder ----
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ---- Stage 2: runtime ----
FROM python:3.11-slim AS runtime

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --shell /usr/sbin/nologin --no-create-home appuser

WORKDIR /app

COPY --from=builder /root/.local /home/appuser/.local
COPY qsg_synthesizer ./qsg_synthesizer
COPY .claude ./.claude
COPY sample_docs ./sample_docs

# ORT_DISABLE_TELEMETRY: see qsg_synthesizer/__init__.py; set here too so
# nothing in the image can load ONNX Runtime with telemetry on.
ENV PATH=/home/appuser/.local/bin:$PATH \
    HISTORY_DIR=/app/data/history \
    ORT_DISABLE_TELEMETRY=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Code and packages stay root-owned (read-only to the app), so an exploited
# app process can't rewrite them. Only the history folder is appuser's, created
# here so a named volume mounted over it inherits that ownership.
RUN mkdir -p /app/data/history \
    && chown appuser:appuser /app/data /app/data/history

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1

ENTRYPOINT ["uvicorn", "qsg_synthesizer.main:app", "--host", "0.0.0.0", "--port", "8000"]
