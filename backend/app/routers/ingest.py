from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import fitz
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

import inngest

from ..auth.deps import get_current_user
from ..config import settings
from ..database import get_db
from ..models import Document, ParseStatus, User, UserAssetAssignment, UserRole
from ..schemas.ingest import BulkIngestFailure, BulkIngestResponse, BulkIngestSuccess, IngestResponse
from ..services.storage import LocalStorage
from ..worker.inngest_client import inngest_client

router = APIRouter(prefix="", tags=["ingest"])


def _sanitize_filename(filename: str | None) -> str:
    if not filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    return Path(filename).name


def _classify_upload(filename: str, content_type: str | None) -> tuple[bool, bool]:
    normalized_content_type = content_type or ""
    ext = Path(filename).suffix.lower()
    is_pdf = normalized_content_type == "application/pdf" or ext == ".pdf"
    is_text = normalized_content_type in ("text/plain", "application/octet-stream") or ext == ".txt"
    return is_pdf, is_text


def _pdf_page_count(content: bytes) -> int:
    try:
        with fitz.open(stream=BytesIO(content), filetype="pdf") as doc:
            return doc.page_count
    except Exception as exc:  # pragma: no cover - surface error
        raise HTTPException(status_code=400, detail=f"Invalid PDF: {exc}") from exc


def _ensure_page_limit(total_pages: int | None) -> None:
    if total_pages is not None and total_pages > settings.max_pages:
        raise HTTPException(
            status_code=400,
            detail=f"Page count {total_pages} exceeds limit {settings.max_pages}",
        )


def _require_asset_access(db: Session, current_user: User, asset_id: UUID) -> None:
    if current_user.role == UserRole.admin:
        return
    assignment = (
        db.query(UserAssetAssignment)
        .filter(
            UserAssetAssignment.user_id == current_user.id,
            UserAssetAssignment.asset_id == asset_id,
        )
        .first()
    )
    if assignment is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this asset")


def _create_document(
    *,
    db: Session,
    asset_id: UUID,
    uploaded_by: UUID,
    filename: str,
    content: bytes,
    content_type: str,
    is_pdf: bool,
    total_pages: int | None,
    sha256: str,
) -> Document:
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
    return document


@router.post("/ingest", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
async def ingest_document(
    asset_id: UUID = Form(...),
    uploaded_by: UUID = Form(...),
    auto_process: bool = Form(default=False),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    filename = _sanitize_filename(file.filename)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty upload")

    content_type = file.content_type or ""
    is_pdf, is_text = _classify_upload(filename, content_type)
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
        total_pages = _pdf_page_count(content)
        _ensure_page_limit(total_pages)

    document = _create_document(
        db=db,
        asset_id=asset_id,
        uploaded_by=uploaded_by,
        filename=filename,
        content=content,
        content_type=content_type,
        is_pdf=is_pdf,
        total_pages=total_pages,
        sha256=sha256,
    )
    db.commit()

    if auto_process:
        await inngest_client.send(
            inngest.Event(
                name="veritas/document.uploaded",
                data={"document_id": str(document.id)},
            )
        )

    return IngestResponse(document_id=document.id)


@router.post("/ingest/bulk", response_model=BulkIngestResponse)
async def ingest_bulk_documents(
    response: Response,
    asset_id: UUID = Form(...),
    uploaded_by: UUID = Form(...),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if uploaded_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="uploaded_by must match authenticated user")
    _require_asset_access(db, current_user, asset_id)

    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")
    if len(files) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 files per request")

    succeeded: list[BulkIngestSuccess] = []
    failed: list[BulkIngestFailure] = []
    document_ids_to_process: list[UUID] = []

    for upload in files:
        filename = Path(upload.filename or "unknown").name
        savepoint = db.begin_nested()
        try:
            filename = _sanitize_filename(upload.filename)
            content = await upload.read()
            if not content:
                raise HTTPException(status_code=400, detail="Empty upload")

            content_type = upload.content_type or ""
            is_pdf, _is_text = _classify_upload(filename, content_type)
            if not is_pdf:
                raise HTTPException(status_code=400, detail="Only PDF uploads are supported")

            sha256 = hashlib.sha256(content).hexdigest()
            existing = (
                db.query(Document)
                .filter(Document.sha256 == sha256, Document.asset_id == asset_id)
                .first()
            )
            if existing:
                succeeded.append(BulkIngestSuccess(filename=filename, document_id=existing.id))
                savepoint.commit()
                continue

            total_pages = _pdf_page_count(content)
            _ensure_page_limit(total_pages)

            document = _create_document(
                db=db,
                asset_id=asset_id,
                uploaded_by=uploaded_by,
                filename=filename,
                content=content,
                content_type=content_type,
                is_pdf=is_pdf,
                total_pages=total_pages,
                sha256=sha256,
            )
            succeeded.append(BulkIngestSuccess(filename=filename, document_id=document.id))
            document_ids_to_process.append(document.id)
            savepoint.commit()
        except HTTPException as exc:
            savepoint.rollback()
            failed.append(BulkIngestFailure(filename=filename, reason=str(exc.detail)))
        except Exception as exc:
            savepoint.rollback()
            failed.append(BulkIngestFailure(filename=filename, reason=str(exc)))

    if not succeeded:
        db.rollback()
        response.status_code = status.HTTP_400_BAD_REQUEST
        return BulkIngestResponse(succeeded=succeeded, failed=failed)

    db.commit()

    for document_id in document_ids_to_process:
        await inngest_client.send(
            inngest.Event(
                name="veritas/document.uploaded",
                data={"document_id": str(document_id)},
            )
        )

    return BulkIngestResponse(succeeded=succeeded, failed=failed)
