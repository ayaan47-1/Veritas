from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import OIDCProvider, User, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login/{provider}")
def start_login(provider: OIDCProvider):
    return {
        "provider": provider.value,
        "login_url": f"/auth/callback?provider={provider.value}&subject=dev-subject",
    }


@router.get("/callback")
def auth_callback(
    provider: OIDCProvider,
    subject: str,
    email: str,
    name: str,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.oidc_provider == provider, User.oidc_subject == subject).first()
    if not user:
        user = User(
            id=uuid.uuid4(),
            email=email,
            name=name,
            oidc_provider=provider,
            oidc_subject=subject,
            role=UserRole.viewer,
            is_active=True,
            last_login_at=datetime.now(tz=timezone.utc),
        )
        db.add(user)
    else:
        user.email = email
        user.name = name
        user.last_login_at = datetime.now(tz=timezone.utc)
        db.add(user)
    db.commit()

    return {
        "access_token": f"dev-token-{user.id}",
        "token_type": "bearer",
        "user_id": str(user.id),
    }
