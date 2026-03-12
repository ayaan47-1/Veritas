from __future__ import annotations

import uuid
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin
from .enums import EntityType


class Entity(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "entities"

    canonical_name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    entity_type: Mapped[EntityType] = mapped_column(Enum(EntityType), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    mentions = relationship("EntityMention", back_populates="entity", foreign_keys="EntityMention.entity_id")


class EntityMention(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "entity_mentions"

    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("entities.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    mentioned_name: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    suggested_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("entities.id"))
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    entity = relationship(
        "Entity",
        back_populates="mentions",
        primaryjoin="Entity.id==EntityMention.entity_id",
        foreign_keys=[entity_id],
    )
