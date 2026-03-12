from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin
from .enums import OIDCProvider, UserRole


class User(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    oidc_provider: Mapped[OIDCProvider] = mapped_column(Enum(OIDCProvider), nullable=False)
    oidc_subject: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    assets = relationship("UserAssetAssignment", back_populates="user")

    __table_args__ = (
        UniqueConstraint("oidc_provider", "oidc_subject", name="uq_user_oidc"),
    )


class UserAssetAssignment(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "user_asset_assignments"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="assets", primaryjoin="User.id==UserAssetAssignment.user_id")
    asset = relationship("Asset", back_populates="users", primaryjoin="Asset.id==UserAssetAssignment.asset_id")

    __table_args__ = (
        UniqueConstraint("user_id", "asset_id", name="uq_user_asset"),
    )
