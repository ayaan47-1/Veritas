from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from ..models.enums import IfcParseStatus


class IfcUploadResponse(BaseModel):
    ifc_model_id: UUID


class IfcModelOut(BaseModel):
    id: UUID
    asset_id: UUID
    source_name: str
    sha256: str
    uploaded_by: UUID
    parse_status: IfcParseStatus
    element_count: Optional[int] = None
    element_types: Optional[dict] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
