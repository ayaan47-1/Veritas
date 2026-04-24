from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from uuid import UUID


class InvalidTokenError(ValueError):
    pass


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def mint_unsubscribe_token(user_id: UUID, secret: str, ttl_days: int = 365) -> str:
    if not secret:
        raise ValueError("secret is required to mint unsubscribe tokens")
    expiry = int(time.time()) + ttl_days * 86400
    payload_text = f"{user_id}|{expiry}"
    payload_bytes = payload_text.encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(signature)}"


def verify_unsubscribe_token(token: str, secret: str) -> UUID:
    if not secret:
        raise InvalidTokenError("secret is required to verify unsubscribe tokens")
    if not token or "." not in token:
        raise InvalidTokenError("malformed token")

    payload_segment, signature_segment = token.split(".", 1)
    try:
        payload_bytes = _b64url_decode(payload_segment)
        provided_signature = _b64url_decode(signature_segment)
    except (ValueError, binascii.Error) as exc:
        raise InvalidTokenError("invalid token encoding") from exc

    expected_signature = hmac.new(
        secret.encode("utf-8"), payload_bytes, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected_signature, provided_signature):
        raise InvalidTokenError("signature mismatch")

    try:
        payload_text = payload_bytes.decode("utf-8")
        user_id_text, expiry_text = payload_text.split("|", 1)
        user_id = UUID(user_id_text)
        expiry = int(expiry_text)
    except (UnicodeDecodeError, ValueError) as exc:
        raise InvalidTokenError("invalid payload") from exc

    if expiry < int(time.time()):
        raise InvalidTokenError("token expired")

    return user_id
