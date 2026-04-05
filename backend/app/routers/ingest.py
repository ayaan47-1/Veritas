from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import fitz
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

import inngest

from ..config import settings
from ..database import get_db
from ..models import Document, ParseStatus
from ..schemas.ingest import IngestResponse
from ..services.storage import LocalStorage
from ..worker.inngest_client import inngest_client

router = APIRouter(prefix="", tags=["ingest"])


@router.post("/ingest", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
async def ingest_document(
    asset_id: UUID = Form(...),
    uploaded_by: UUID = Form(...),
    auto_process: bool = Form(default=False),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    filename = Path(file.filename).name
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty upload")

    content_type = file.content_type or ""
    ext = Path(filename).suffix.lower()
    is_pdf = content_type == "application/pdf" or ext == ".pdf"
    is_text = content_type in ("text/plain", "application/octet-stream") or ext == ".txt"
    if not (is_pdf or is_text):
        raise HTTPException(status_code=400, detail="Only PDF or .txt uploads are supported")

    sha256 = hashlib.sha256(content).hexdigest()
    existing = (
        db.query(Document)
        .filter(Document.sha256 == sha256, Document.asset_id == asset_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Duplicate document (sha256 match)")

    total_pages = None
    if is_pdf:
        try:
            with fitz.open(stream=BytesIO(content), filetype="pdf") as doc:
                total_pages = doc.page_count
        except Exception as exc:  # pragma: no cover - surface error
            raise HTTPException(status_code=400, detail=f"Invalid PDF: {exc}") from exc

        if total_pages is not None and total_pages > settings.max_pages:
            raise HTTPException(
                status_code=400,
                detail=f"Page count {total_pages} exceeds limit {settings.max_pages}",
            )

    document_id = uuid4()
    storage = LocalStorage(settings.data_dir)
    relative_path = f"originals/{document_id}/{filename}"
    file_path = storage.save(relative_path, content)

    document = Document(
        id=document_id,
        asset_id=asset_id,
        source_name=filename,
        file_path=file_path,
        processed_file_path=None,
        sha256=sha256,
        mime_type=content_type or ("application/pdf" if is_pdf else "text/plain"),
        uploaded_by=uploaded_by,
        parse_status=ParseStatus.uploaded,
        total_pages=total_pages,
    )
    db.add(document)
    db.commit()

    if auto_process:
        await inngest_client.send(
            inngest.Event(
                name="veritas/document.uploaded",
                data={"document_id": str(document_id)},
            )
        )

    return IngestResponse(document_id=document_id)
