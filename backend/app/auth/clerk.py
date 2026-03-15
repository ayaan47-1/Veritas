from __future__ import annotations

import os

import jwt
from jwt import PyJWKClient


class ClerkAuthError(RuntimeError):
    pass


_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        url = os.environ.get("CLERK_JWKS_URL")
        if not url:
            raise ClerkAuthError("CLERK_JWKS_URL not configured")
        _jwks_client = PyJWKClient(url)
    return _jwks_client


def verify_clerk_token(token: str) -> dict:
    issuer = os.environ.get("CLERK_ISSUER")
    if not issuer:
        raise ClerkAuthError("CLERK_ISSUER not configured")
    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_iss": True},
            issuer=issuer,
        )
    except jwt.exceptions.PyJWTError as exc:
        raise ClerkAuthError(str(exc)) from exc
    return payload
