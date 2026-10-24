"""FastAPI entrypoint for the QSG Synthesizer service."""

from __future__ import annotations

import os

from dotenv import load_dotenv

# Must run before any app.* import below: several modules (app.services.history's
# HISTORY_DIR, app.logging_config's LOG_LEVEL) read os.environ at import/call time,
# so .env has to be loaded into the process environment first. Skipped under
# pytest (PYTEST_VERSION is set by pytest>=7.2 for exactly this check) so the
# test suite never picks up a developer's real API keys from .env and starts
# making live LLM calls - see README's "Tests" section, which promises fully
# offline/deterministic runs. (Ruff's E402 is disabled for this file in
# pyproject.toml since these imports intentionally follow this guard.)
if "PYTEST_VERSION" not in os.environ:
    load_dotenv()

import base64
import logging
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.logging_config import configure_logging
from app.schemas import BattlecardResponse, HistoryPage, HistoryRecord
from app.services import export as export_service
from app.services import history
from app.services.auth import require_auth, security, verify_credentials
from app.services.orchestrator import extract_device_spec, synthesize_battlecard
from app.services.rate_limiter import RateLimiter
from app.tools.pdf_parser import extract_text_from_pdf

configure_logging()
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "app" / "static"
SAMPLE_DOCS_DIR = BASE_DIR / "sample_docs"

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB per PDF
MIN_TEXT_CHARS = 50  # below this (ignoring whitespace) there is nothing to extract from
MAX_TEXT_CHARS = 2_000_000  # cap for compare-text's raw device_*_text fields

# Compare endpoints run the extraction+synthesis pipeline (the expensive
# path); export endpoints only render an already-saved battlecard, so they
# get a looser limit.
compare_rate_limiter = RateLimiter(max_requests=10, window_seconds=60)
export_rate_limiter = RateLimiter(max_requests=20, window_seconds=60)

app = FastAPI(
    title="QSG Synthesizer",
    description="Automated Hardware Spec & Quick Start Guide Comparison Engine",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def guard_static_files(request: Request, call_next):
    # StaticFiles is mounted directly on `app`, so it never passes through
    # `router`'s dependencies=[Depends(require_auth)] — gate it here instead,
    # so a configured Basic Auth also covers the dashboard's JS/CSS.
    if request.url.path.startswith("/static/"):
        credentials = await security(request)
        client_host = request.client.host if request.client else "unknown"
        try:
            verify_credentials(credentials, client_host=client_host)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers or {},
            )
    return await call_next(request)


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# /healthz is intentionally left off this router: Docker's HEALTHCHECK and
# the CI job curl it without credentials, and it exposes nothing sensitive.
router = APIRouter(dependencies=[Depends(require_auth)])


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "healthy"}


@router.get("/", include_in_schema=False)
def serve_dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/v1/samples")
def get_samples() -> dict:
    """Base64-encoded sample QSG PDFs, for the dashboard's 1-click demo."""

    def _encode(filename: str) -> dict:
        path = SAMPLE_DOCS_DIR / filename
        if not path.exists():
            raise HTTPException(
                status_code=404, detail=f"Sample '{filename}' not found."
            )
        return {
            "filename": filename,
            "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        }

    return {
        "device_a": _encode("device_a_qsg.pdf"),
        "device_b": _encode("device_b_qsg.pdf"),
    }


class CompareTextRequest(BaseModel):
    device_a_text: str = Field(
        ...,
        max_length=MAX_TEXT_CHARS,
        description="Raw JSON or Markdown QSG text for Device A",
    )
    device_a_name: str = "device_a"
    device_b_text: str = Field(
        ...,
        max_length=MAX_TEXT_CHARS,
        description="Raw JSON or Markdown QSG text for Device B",
    )
    device_b_name: str = "device_b"


@router.post(
    "/api/v1/compare-text",
    response_model=BattlecardResponse,
    dependencies=[Depends(compare_rate_limiter)],
)
def compare_text(payload: CompareTextRequest) -> BattlecardResponse:
    _require_text(payload.device_a_text, payload.device_a_name)
    _require_text(payload.device_b_text, payload.device_b_name)
    device_a = extract_device_spec(payload.device_a_text, payload.device_a_name)
    device_b = extract_device_spec(payload.device_b_text, payload.device_b_name)
    battlecard = synthesize_battlecard(device_a, device_b)
    history.save_comparison(battlecard)
    return battlecard


def _require_text(text: str, name: str) -> str:
    """Reject documents with no usable text rather than scoring an empty device."""
    if len("".join(text.split())) < MIN_TEXT_CHARS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{name}' contains no extractable text "
                "(is it a scanned or image-only document?)."
            ),
        )
    return text


def _read_pdf_upload(upload: UploadFile) -> str:
    if not (upload.filename or "").lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400, detail=f"'{upload.filename}' is not a PDF file."
        )
    contents = upload.file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        max_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"'{upload.filename}' exceeds the {max_mb}MB upload limit.",
        )
    try:
        text = extract_text_from_pdf(contents)
    except Exception:
        logger.warning(
            "Could not parse upload '%s' as a PDF", upload.filename, exc_info=True
        )
        raise HTTPException(
            status_code=400, detail=f"'{upload.filename}' could not be read as a PDF."
        )
    return _require_text(text, upload.filename or "upload")


@router.post(
    "/api/v1/compare-pdfs",
    response_model=BattlecardResponse,
    dependencies=[Depends(compare_rate_limiter)],
)
def compare_pdfs(
    device_a_pdf: UploadFile = File(...),
    device_b_pdf: UploadFile = File(...),
) -> BattlecardResponse:
    device_a_text = _read_pdf_upload(device_a_pdf)
    device_b_text = _read_pdf_upload(device_b_pdf)

    device_a = extract_device_spec(
        device_a_text, device_a_pdf.filename or "device_a.pdf"
    )
    device_b = extract_device_spec(
        device_b_text, device_b_pdf.filename or "device_b.pdf"
    )

    battlecard = synthesize_battlecard(device_a, device_b)
    history.save_comparison(battlecard)
    return battlecard


@router.get("/api/v1/history", response_model=HistoryPage)
def get_history(
    limit: int = Query(20, ge=1, le=history.MAX_HISTORY_ENTRIES),
    offset: int = Query(0, ge=0),
    q: Optional[str] = Query(None, max_length=200),
) -> HistoryPage:
    return HistoryPage(
        items=history.list_comparisons(limit=limit, offset=offset, query=q),
        total=history.count_comparisons(query=q),
        limit=limit,
        offset=offset,
    )


def _get_history_record_or_404(history_id: str) -> HistoryRecord:
    record = history.get_comparison(history_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"History item '{history_id}' not found."
        )
    return record


@router.get("/api/v1/history/{history_id}", response_model=HistoryRecord)
def get_history_item(history_id: str) -> HistoryRecord:
    return _get_history_record_or_404(history_id)


@router.get(
    "/api/v1/history/{history_id}/export.md",
    include_in_schema=False,
    dependencies=[Depends(export_rate_limiter)],
)
def export_history_markdown(history_id: str) -> Response:
    record = _get_history_record_or_404(history_id)
    markdown = export_service.battlecard_to_markdown(record.battlecard)
    return Response(
        content=markdown,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="battlecard-{history_id}.md"'
        },
    )


@router.get(
    "/api/v1/history/{history_id}/export.pdf",
    include_in_schema=False,
    dependencies=[Depends(export_rate_limiter)],
)
def export_history_pdf(history_id: str) -> Response:
    record = _get_history_record_or_404(history_id)
    pdf_bytes = export_service.battlecard_to_pdf_bytes(record.battlecard)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="battlecard-{history_id}.pdf"'
        },
    )


@router.delete("/api/v1/history/{history_id}", status_code=204, response_class=Response)
def delete_history_item(history_id: str) -> Response:
    if not history.delete_comparison(history_id):
        raise HTTPException(
            status_code=404, detail=f"History item '{history_id}' not found."
        )
    return Response(status_code=204)


@router.delete("/api/v1/history")
def clear_all_history() -> dict:
    return {"deleted": history.clear_history()}


app.include_router(router)
