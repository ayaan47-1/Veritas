from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class IngestResponse(BaseModel):
    document_id: UUID

