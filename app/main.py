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
import hashlib
import logging
from pathlib import Path
from typing import Literal, Optional

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.logging_config import configure_logging
from app.schemas import (
    CompareResponse,
    DeviceSpec,
    HistoryPage,
    HistoryRecord,
    HistoryStackPage,
    Perspective,
)
from app.services import export as export_service
from app.services import history
from app.services.auth import (
    auth_enabled,
    require_auth,
    security,
    verify_credentials,
)
from app.services.orchestrator import (
    LLMOverride,
    extract_device_specs,
    server_llm_provider,
    synthesize_battlecard,
)
from app.services.rate_limiter import RateLimiter
from app.tools.document_parser import (
    DocumentParseError,
    extract_text,
    file_kind,
    is_supported,
)

configure_logging()
logger = logging.getLogger(__name__)
if not auth_enabled():
    logger.warning(
        "APP_USERNAME/APP_PASSWORD are not set: the dashboard and API are open "
        "to anyone who can reach this server. Set both before exposing it "
        "beyond localhost."
    )

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "app" / "static"
SAMPLE_DOCS_DIR = BASE_DIR / "sample_docs"

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB per uploaded document
MIN_TEXT_CHARS = 50  # below this (ignoring whitespace) there is nothing to extract from
MAX_TEXT_CHARS = 2_000_000  # cap for compare-text's raw device_*_text fields
MAX_API_KEY_CHARS = 300
LLM_PROVIDER_CHOICES = ("anthropic", "gemini", "heuristic")

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

# No CORS middleware on purpose: the dashboard is served from this same
# origin, and allowing other origins would let any page open in the browser
# call the API (clear history, spend the server's LLM key) while auth is off.

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# FastAPI registers its docs routes directly on `app`, outside `router`.
_DOCS_PATHS = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}


@app.middleware("http")
async def guard_static_files(request: Request, call_next):
    # StaticFiles and FastAPI's built-in docs routes are registered directly on
    # `app`, so they never pass through `router`'s dependencies=[Depends(require_auth)]
    # — gate them here instead, so a configured Basic Auth also covers the
    # dashboard's JS/CSS and the API docs/schema.
    if request.url.path.startswith("/static/") or request.url.path in _DOCS_PATHS:
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

    # Served under product names, like real uploads, so they read right in
    # either mode; the repo files keep their device_a/device_b names.
    def _encode(filename: str, served_name: str) -> dict:
        path = SAMPLE_DOCS_DIR / filename
        if not path.exists():
            raise HTTPException(
                status_code=404, detail=f"Sample '{filename}' not found."
            )
        return {
            "filename": served_name,
            "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        }

    return {
        "device_a": _encode("device_a_qsg.pdf", "NovaFiber-GX400-QSG.pdf"),
        "device_b": _encode("device_b_qsg.pdf", "LegacyLink-RG-210-QSG.pdf"),
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
    response_model=CompareResponse,
    dependencies=[Depends(compare_rate_limiter)],
)
def compare_text(payload: CompareTextRequest) -> CompareResponse:
    _require_text(payload.device_a_text, payload.device_a_name)
    _require_text(payload.device_b_text, payload.device_b_name)
    device_a, device_b = extract_device_specs(
        payload.device_a_text,
        payload.device_a_name,
        payload.device_b_text,
        payload.device_b_name,
    )
    sources = [
        (payload.device_a_name, payload.device_a_text),
        (payload.device_b_name, payload.device_b_text),
    ]
    return _synthesize_and_save(device_a, device_b, sources)


def _input_fingerprint(text_a: str, text_b: str) -> str:
    """Identifies a comparison's inputs, so history can spot exact reruns."""
    return hashlib.sha256(f"{text_a}\0{text_b}".encode("utf-8")).hexdigest()


def _synthesize_and_save(
    device_a: DeviceSpec,
    device_b: DeviceSpec,
    sources: list[tuple[str, str]],
    perspective: Optional[Perspective] = None,
    keep_documents: bool = True,
) -> CompareResponse:
    """`sources` are the (name, text) inputs, A then B, kept for reruns
    unless `keep_documents` is off."""
    battlecard = synthesize_battlecard(device_a, device_b, perspective)
    (_, text_a), (_, text_b) = sources
    record = history.save_comparison(
        battlecard,
        input_fingerprint=_input_fingerprint(text_a, text_b),
        sources=sources if keep_documents else (),
    )
    return CompareResponse(**battlecard.model_dump(), history_id=record.id)


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


SUPPORTED_TYPES_HINT = "Use a PDF, Word (.docx), Markdown or text file."


def _read_document_upload(upload: UploadFile, pdf_only: bool = False) -> str:
    filename = upload.filename or ""
    if pdf_only:
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=400, detail=f"'{filename}' is not a PDF file."
            )
    elif not is_supported(filename):
        raise HTTPException(
            status_code=400,
            detail=f"'{filename}' is not a supported file type. {SUPPORTED_TYPES_HINT}",
        )
    contents = upload.file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        max_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"'{filename}' exceeds the {max_mb}MB upload limit.",
        )
    try:
        text = extract_text(filename, contents)
    except DocumentParseError:
        logger.warning("Could not parse upload '%s'", filename, exc_info=True)
        raise HTTPException(
            status_code=400,
            detail=f"'{filename}' could not be read as a {file_kind(filename)}.",
        )
    return _require_text(text, filename or "upload")


@router.post(
    "/api/v1/compare-pdfs",
    response_model=CompareResponse,
    dependencies=[Depends(compare_rate_limiter)],
)
def compare_pdfs(
    device_a_pdf: UploadFile = File(...),
    device_b_pdf: UploadFile = File(...),
) -> CompareResponse:
    device_a_text = _read_document_upload(device_a_pdf, pdf_only=True)
    device_b_text = _read_document_upload(device_b_pdf, pdf_only=True)
    name_a = device_a_pdf.filename or "device_a.pdf"
    name_b = device_b_pdf.filename or "device_b.pdf"

    device_a, device_b = extract_device_specs(
        device_a_text, name_a, device_b_text, name_b
    )
    return _synthesize_and_save(
        device_a, device_b, [(name_a, device_a_text), (name_b, device_b_text)]
    )


def _resolve_document(
    label: str,
    file: Optional[UploadFile],
    text: Optional[str],
    name: Optional[str],
) -> tuple[str, str]:
    """Return (document text, source name) from exactly one of a file or pasted text."""
    has_file = file is not None and bool(file.filename)
    has_text = bool(text and text.strip())
    if has_file == has_text:
        raise HTTPException(
            status_code=422,
            detail=f"Add either a file or pasted text for {label}, not both.",
        )
    if file is not None and has_file:
        return _read_document_upload(file), file.filename or "upload"
    source_name = (name or "").strip() or "Pasted text"
    return _require_text(text or "", source_name), source_name


def _llm_override(
    provider: Optional[str], api_key: Optional[str]
) -> Optional[LLMOverride]:
    """Build the request's extractor choice from the dashboard's settings
    headers. Validated by hand so a rejected key never appears in the error."""
    if not provider:
        if api_key:
            raise HTTPException(
                status_code=422, detail="Choose an AI provider to use with the key."
            )
        return None
    provider = provider.strip().lower()
    if provider not in LLM_PROVIDER_CHOICES:
        raise HTTPException(
            status_code=422,
            detail="Unknown AI provider. Use anthropic, gemini or heuristic.",
        )
    key = (api_key or "").strip() or None
    if key is not None and (
        len(key) > MAX_API_KEY_CHARS or not all(33 <= ord(ch) <= 126 for ch in key)
    ):
        raise HTTPException(
            status_code=422,
            detail="That API key doesn't look valid. Paste it again in Settings.",
        )
    return LLMOverride(provider=provider, api_key=key)  # type: ignore[arg-type]


@router.get("/api/v1/settings")
def get_settings() -> dict:
    """What "Server default" means in the dashboard's settings."""
    return {"server_provider": server_llm_provider()}


@router.post(
    "/api/v1/compare",
    response_model=CompareResponse,
    dependencies=[Depends(compare_rate_limiter)],
)
def compare_documents(
    device_a_file: Optional[UploadFile] = File(None),
    device_a_text: Optional[str] = Form(None, max_length=MAX_TEXT_CHARS),
    device_a_name: Optional[str] = Form(None, max_length=200),
    device_b_file: Optional[UploadFile] = File(None),
    device_b_text: Optional[str] = Form(None, max_length=MAX_TEXT_CHARS),
    device_b_name: Optional[str] = Form(None, max_length=200),
    mode: Literal["sales", "neutral"] = Form("sales"),
    keep_documents: bool = Form(True),
    llm_provider: Optional[str] = Header(None, alias="X-LLM-Provider"),
    llm_api_key: Optional[str] = Header(None, alias="X-LLM-API-Key"),
) -> CompareResponse:
    llm = _llm_override(llm_provider, llm_api_key)
    text_a, source_a = _resolve_document(
        "Device A", device_a_file, device_a_text, device_a_name
    )
    text_b, source_b = _resolve_document(
        "Device B", device_b_file, device_b_text, device_b_name
    )
    device_a, device_b = extract_device_specs(text_a, source_a, text_b, source_b, llm)
    return _synthesize_and_save(
        device_a,
        device_b,
        [(source_a, text_a), (source_b, text_b)],
        mode,
        keep_documents,
    )


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


@router.get("/api/v1/history/stacks", response_model=HistoryStackPage)
def get_history_stacks(
    limit: int = Query(20, ge=1, le=history.MAX_HISTORY_ENTRIES),
    offset: int = Query(0, ge=0),
    q: Optional[str] = Query(None, max_length=200),
) -> HistoryStackPage:
    stacks, total, record_total = history.list_stacks(
        limit=limit, offset=offset, query=q
    )
    return HistoryStackPage(
        items=stacks,
        total=total,
        record_total=record_total,
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
    "/api/v1/history/{history_id}/export-name",
    include_in_schema=False,
    dependencies=[Depends(export_rate_limiter)],
)
def export_default_name(history_id: str) -> dict:
    """Default export file name (no extension), used unless the caller renames it."""
    record = _get_history_record_or_404(history_id)
    return {
        "basename": export_service.default_export_basename(
            record.battlecard, record.created_at
        )
    }


def _export_headers(record: HistoryRecord, ext: str, requested: Optional[str]) -> dict:
    filename = export_service.sanitize_export_filename(
        requested,
        ext,
        export_service.default_export_basename(record.battlecard, record.created_at),
    )
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


@router.get(
    "/api/v1/history/{history_id}/export.md",
    include_in_schema=False,
    dependencies=[Depends(export_rate_limiter)],
)
def export_history_markdown(
    history_id: str, filename: Optional[str] = Query(None, max_length=200)
) -> Response:
    record = _get_history_record_or_404(history_id)
    markdown = export_service.battlecard_to_markdown(record.battlecard)
    return Response(
        content=markdown,
        media_type="text/markdown",
        headers=_export_headers(record, "md", filename),
    )


@router.get(
    "/api/v1/history/{history_id}/export.pdf",
    include_in_schema=False,
    dependencies=[Depends(export_rate_limiter)],
)
def export_history_pdf(
    history_id: str, filename: Optional[str] = Query(None, max_length=200)
) -> Response:
    record = _get_history_record_or_404(history_id)
    pdf_bytes = export_service.battlecard_to_pdf_bytes(record.battlecard)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers=_export_headers(record, "pdf", filename),
    )


# Registered before /history/{history_id} so "documents" isn't taken for an id.
@router.delete("/api/v1/history/documents")
def forget_history_documents() -> dict:
    return {"forgotten": history.forget_all_sources()}


@router.delete("/api/v1/history/{history_id}", status_code=204, response_class=Response)
def delete_history_item(history_id: str) -> Response:
    if not history.delete_comparison(history_id):
        raise HTTPException(
            status_code=404, detail=f"History item '{history_id}' not found."
        )
    return Response(status_code=204)


@router.post(
    "/api/v1/history/{history_id}/rerun",
    response_model=CompareResponse,
    dependencies=[Depends(compare_rate_limiter)],
)
def rerun_history_item(
    history_id: str,
    llm_provider: Optional[str] = Header(None, alias="X-LLM-Provider"),
    llm_api_key: Optional[str] = Header(None, alias="X-LLM-API-Key"),
) -> CompareResponse:
    """Read a saved comparison's documents again (with the request's AI
    settings) and save the result as a new record."""
    llm = _llm_override(llm_provider, llm_api_key)
    record = _get_history_record_or_404(history_id)
    sources = history.load_sources(record)
    if sources is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This comparison's documents weren't kept, so it can't be "
                "rerun. Upload the documents again to compare them."
            ),
        )
    (name_a, text_a), (name_b, text_b) = sources
    device_a, device_b = extract_device_specs(text_a, name_a, text_b, name_b, llm)
    return _synthesize_and_save(
        device_a, device_b, sources, record.battlecard.perspective
    )


@router.delete("/api/v1/history/{history_id}/copies")
def delete_history_copies(history_id: str) -> dict:
    deleted = history.delete_older_copies(history_id)
    if deleted is None:
        raise HTTPException(
            status_code=404, detail=f"History item '{history_id}' not found."
        )
    return {"deleted": deleted}


@router.delete("/api/v1/history")
def clear_all_history() -> dict:
    return {"deleted": history.clear_history()}


app.include_router(router)
