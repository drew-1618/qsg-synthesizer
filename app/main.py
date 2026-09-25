"""FastAPI entrypoint for the QSG Synthesizer service."""

from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.schemas import BattlecardResponse
from app.services.orchestrator import extract_device_spec, synthesize_battlecard
from app.tools.pdf_parser import extract_text_from_pdf

app = FastAPI(title="QSG Synthesizer", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "healthy"}


class CompareTextRequest(BaseModel):
    device_a_text: str = Field(..., description="Raw JSON or Markdown QSG text for Device A")
    device_a_name: str = "device_a"
    device_b_text: str = Field(..., description="Raw JSON or Markdown QSG text for Device B")
    device_b_name: str = "device_b"


@app.post("/api/v1/compare-text", response_model=BattlecardResponse)
def compare_text(payload: CompareTextRequest) -> BattlecardResponse:
    device_a = extract_device_spec(payload.device_a_text, payload.device_a_name)
    device_b = extract_device_spec(payload.device_b_text, payload.device_b_name)
    return synthesize_battlecard(device_a, device_b)


def _read_pdf_upload(upload: UploadFile) -> str:
    if not (upload.filename or "").lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400, detail=f"'{upload.filename}' is not a PDF file."
        )
    return extract_text_from_pdf(upload.file.read())


@app.post("/api/v1/compare-pdfs", response_model=BattlecardResponse)
def compare_pdfs(
    device_a_pdf: UploadFile = File(...),
    device_b_pdf: UploadFile = File(...),
) -> BattlecardResponse:
    device_a_text = _read_pdf_upload(device_a_pdf)
    device_b_text = _read_pdf_upload(device_b_pdf)

    device_a = extract_device_spec(device_a_text, device_a_pdf.filename or "device_a.pdf")
    device_b = extract_device_spec(device_b_text, device_b_pdf.filename or "device_b.pdf")

    return synthesize_battlecard(device_a, device_b)
