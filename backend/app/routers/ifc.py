from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import IfcModel
from ..models.enums import IfcParseStatus
from ..schemas.ifc import IfcModelOut, IfcUploadResponse
from ..services.storage import LocalStorage
from ..config import settings

router = APIRouter(prefix="/ifc", tags=["ifc"])


def _get_ifc_model_or_404(model_id: UUID, db: Session) -> IfcModel:
    model = db.query(IfcModel).filter(IfcModel.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="IFC model not found")
    return model


@router.post(
    "/upload",
    response_model=IfcUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_ifc(
    asset_id: UUID = Form(...),
    uploaded_by: UUID = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> IfcUploadResponse:
    """Upload a .ifc file and register it for compliance checking."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    ext = Path(file.filename).suffix.lower()
    if ext != ".ifc":
        raise HTTPException(status_code=400, detail="Only .ifc files are supported")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty upload")

    sha256 = hashlib.sha256(content).hexdigest()
    existing = db.query(IfcModel).filter(IfcModel.sha256 == sha256).first()
    if existing:
        raise HTTPException(
            status_code=409, detail="Duplicate IFC file (sha256 match)"
        )

    model_id = uuid4()
    storage = LocalStorage(settings.data_dir)
    filename = Path(file.filename).name
    relative_path = f"ifc/{model_id}/{filename}"
    file_path = storage.save(relative_path, content)

    ifc_model = IfcModel(
        id=model_id,
        asset_id=asset_id,
        source_name=filename,
        file_path=file_path,
        sha256=sha256,
        uploaded_by=uploaded_by,
        parse_status=IfcParseStatus.uploaded,
    )
    db.add(ifc_model)
    db.commit()

    return IfcUploadResponse(ifc_model_id=model_id)


@router.get("/{model_id}", response_model=IfcModelOut)
def get_ifc_model(
    model_id: UUID,
    db: Session = Depends(get_db),
):
    """Get metadata for a previously uploaded IFC model."""
    return _get_ifc_model_or_404(model_id, db)
