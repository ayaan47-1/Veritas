from .assets import router as assets
from .auth import router as auth
from .compliance import router as compliance
from .config import router as config
from .documents import router as documents
from .entities import router as entities
from .exports import router as exports
from .ifc import router as ifc
from .ingest import router as ingest
from .notifications import router as notifications
from .obligations import router as obligations
from .risks import router as risks
from .summaries import router as summaries
from .users import router as users

__all__ = [
    "assets",
    "auth",
    "compliance",
    "config",
    "documents",
    "entities",
    "exports",
    "ifc",
    "ingest",
    "notifications",
    "obligations",
    "risks",
    "summaries",
    "users",
]
