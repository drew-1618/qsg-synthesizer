"""FastAPI entrypoint for the QSG Synthesizer service."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.schemas import BattlecardResponse, HistoryRecord, HistorySummary
from app.services import export as export_service
from app.services import history
from app.services.auth import require_auth
from app.services.orchestrator import extract_device_spec, synthesize_battlecard
from app.services.rate_limiter import RateLimiter
from app.tools.pdf_parser import extract_text_from_pdf

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "app" / "static"
SAMPLE_DOCS_DIR = BASE_DIR / "sample_docs"

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB per PDF

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
            raise HTTPException(status_code=404, detail=f"Sample '{filename}' not found.")
        return {
            "filename": filename,
            "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        }

    return {
        "device_a": _encode("device_a_qsg.pdf"),
        "device_b": _encode("device_b_qsg.pdf"),
    }


class CompareTextRequest(BaseModel):
    device_a_text: str = Field(..., description="Raw JSON or Markdown QSG text for Device A")
    device_a_name: str = "device_a"
    device_b_text: str = Field(..., description="Raw JSON or Markdown QSG text for Device B")
    device_b_name: str = "device_b"


@router.post(
    "/api/v1/compare-text",
    response_model=BattlecardResponse,
    dependencies=[Depends(compare_rate_limiter)],
)
def compare_text(payload: CompareTextRequest) -> BattlecardResponse:
    device_a = extract_device_spec(payload.device_a_text, payload.device_a_name)
    device_b = extract_device_spec(payload.device_b_text, payload.device_b_name)
    battlecard = synthesize_battlecard(device_a, device_b)
    history.save_comparison(battlecard)
    return battlecard


def _read_pdf_upload(upload: UploadFile) -> str:
    if not (upload.filename or "").lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400, detail=f"'{upload.filename}' is not a PDF file."
        )
    contents = upload.file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"'{upload.filename}' exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB upload limit.",
        )
    return extract_text_from_pdf(contents)


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

    device_a = extract_device_spec(device_a_text, device_a_pdf.filename or "device_a.pdf")
    device_b = extract_device_spec(device_b_text, device_b_pdf.filename or "device_b.pdf")

    battlecard = synthesize_battlecard(device_a, device_b)
    history.save_comparison(battlecard)
    return battlecard


@router.get("/api/v1/history", response_model=List[HistorySummary])
def get_history() -> List[HistorySummary]:
    return history.list_comparisons()


def _get_history_record_or_404(history_id: str) -> HistoryRecord:
    record = history.get_comparison(history_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"History item '{history_id}' not found.")
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
        headers={"Content-Disposition": f'attachment; filename="battlecard-{history_id}.md"'},
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
        headers={"Content-Disposition": f'attachment; filename="battlecard-{history_id}.pdf"'},
    )


@router.delete("/api/v1/history/{history_id}", status_code=204, response_class=Response)
def delete_history_item(history_id: str) -> Response:
    if not history.delete_comparison(history_id):
        raise HTTPException(status_code=404, detail=f"History item '{history_id}' not found.")
    return Response(status_code=204)


@router.delete("/api/v1/history")
def clear_all_history() -> dict:
    return {"deleted": history.clear_history()}


app.include_router(router)
