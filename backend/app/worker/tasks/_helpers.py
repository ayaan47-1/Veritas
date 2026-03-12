from __future__ import annotations

from sqlalchemy.orm import Session

from ...database import SessionLocal
from ...models import Document, ParseStatus


def update_parse_status(document_id: str, status: ParseStatus) -> None:
    db: Session = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            return
        if document.parse_status == ParseStatus.failed and status != ParseStatus.failed:
            return
        document.parse_status = status
        db.add(document)
        db.commit()
    finally:
        db.close()
