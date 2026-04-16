from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Document, Obligation, OIDCProvider, Risk, User, UserAssetAssignment, UserRole
from .clerk import ClerkAuthError, verify_clerk_token


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authentication")
    token = auth_header.removeprefix("Bearer ").strip()
    try:
        payload = verify_clerk_token(token)
    except ClerkAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims")
    user = db.query(User).filter(
        User.oidc_provider == OIDCProvider.clerk,
        User.oidc_subject == sub,
    ).first()
    if not user:
        user = User(
            id=uuid4(),
            oidc_provider=OIDCProvider.clerk,
            oidc_subject=sub,
            email=payload.get("email") or f"{sub}@clerk.local",
            name=payload.get("name", sub),
            role=UserRole.viewer,
            is_active=True,
            last_login_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.commit()
    elif not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account inactive")
    else:
        user.last_login_at = datetime.now(timezone.utc)
        jwt_email = payload.get("email")
        jwt_name = payload.get("name")
        if jwt_email and user.email != jwt_email:
            user.email = jwt_email
        if jwt_name and user.name != jwt_name:
            user.name = jwt_name
        db.commit()
    return user


def require_authenticated(current_user: User = Depends(get_current_user)) -> User:
    return current_user


def require_reviewer_or_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in {UserRole.reviewer, UserRole.admin}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Reviewer or admin role required")
    return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return current_user


def _ensure_asset_access(db: Session, current_user: User, asset_id: UUID) -> None:
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


def require_asset_scope(asset_param: str, required_for_non_admin: bool = False) -> Callable:
    def _dependency(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        raw_asset_id = request.path_params.get(asset_param) or request.query_params.get(asset_param)
        if not raw_asset_id:
            if required_for_non_admin and current_user.role != UserRole.admin:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="asset_id required")
            return current_user
        try:
            asset_id = UUID(raw_asset_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid asset_id") from exc
        _ensure_asset_access(db, current_user, asset_id)
        return current_user

    return _dependency


def require_obligation_access(obligation_param: str) -> Callable:
    def _dependency(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        raw_id = request.path_params.get(obligation_param)
        if not raw_id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Missing obligation id")
        try:
            obligation_id = UUID(raw_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid obligation id") from exc

        obligation = db.query(Obligation).filter(Obligation.id == obligation_id).first()
        if obligation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Obligation not found")
        document = db.query(Document).filter(Document.id == obligation.document_id).first()
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        _ensure_asset_access(db, current_user, document.asset_id)
        return current_user

    return _dependency


def require_risk_access(risk_param: str) -> Callable:
    def _dependency(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        raw_id = request.path_params.get(risk_param)
        if not raw_id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Missing risk id")
        try:
            risk_id = UUID(raw_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid risk id") from exc

        risk = db.query(Risk).filter(Risk.id == risk_id).first()
        if risk is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Risk not found")
        document = db.query(Document).filter(Document.id == risk.document_id).first()
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        _ensure_asset_access(db, current_user, document.asset_id)
        return current_user

    return _dependency


def require_requested_user_access(user_param: str) -> Callable:
    def _dependency(request: Request, current_user: User = Depends(get_current_user)) -> User:
        raw_requested = request.path_params.get(user_param) or request.query_params.get(user_param)
        if not raw_requested:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Missing user id")
        try:
            requested_id = UUID(raw_requested)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid user id") from exc

        if current_user.role != UserRole.admin and requested_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden user access")
        return current_user

    return _dependency
