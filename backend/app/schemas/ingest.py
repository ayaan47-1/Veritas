from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class IngestResponse(BaseModel):
    document_id: UUID


class BulkIngestSuccess(BaseModel):
    filename: str
    document_id: UUID


class BulkIngestFailure(BaseModel):
    filename: str
    reason: str


class BulkIngestResponse(BaseModel):
    succeeded: list[BulkIngestSuccess]
    failed: list[BulkIngestFailure]
