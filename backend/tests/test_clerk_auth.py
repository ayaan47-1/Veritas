from __future__ import annotations

import pytest
import jwt.exceptions
from unittest.mock import MagicMock

import backend.app.auth.clerk as clerk_module
from backend.app.auth.clerk import ClerkAuthError, verify_clerk_token


@pytest.fixture(autouse=True)
def reset_jwks_singleton():
    """Reset the module-level JWKS client singleton between tests."""
    original = clerk_module._jwks_client
    clerk_module._jwks_client = None
    yield
    clerk_module._jwks_client = original


def test_invalid_token_raises_clerk_auth_error(monkeypatch):
    fake_client = MagicMock()
    fake_client.get_signing_key_from_jwt.side_effect = jwt.exceptions.InvalidTokenError("bad token")

    monkeypatch.setenv("CLERK_JWKS_URL", "https://test.clerk.dev/.well-known/jwks.json")
    monkeypatch.setenv("CLERK_ISSUER", "https://test.clerk.dev")
    monkeypatch.setattr(clerk_module, "_get_jwks_client", lambda: fake_client)

    with pytest.raises(ClerkAuthError):
        verify_clerk_token("not-a-valid-token")


def test_verify_calls_jwks_client(monkeypatch):
    fake_key = object()
    fake_client = MagicMock()
    fake_client.get_signing_key_from_jwt.return_value = MagicMock(key=fake_key)

    payload = {"sub": "user_123", "email": "a@b.com", "name": "Alice", "iss": "https://test.clerk.dev"}

    monkeypatch.setenv("CLERK_JWKS_URL", "https://test.clerk.dev/.well-known/jwks.json")
    monkeypatch.setenv("CLERK_ISSUER", "https://test.clerk.dev")
    monkeypatch.setattr(clerk_module, "_get_jwks_client", lambda: fake_client)
    monkeypatch.setattr(clerk_module.jwt, "decode", lambda *args, **kwargs: payload)

    result = verify_clerk_token("some.jwt.token")

    assert result["sub"] == "user_123"
    fake_client.get_signing_key_from_jwt.assert_called_once_with("some.jwt.token")


def test_wrong_issuer_raises(monkeypatch):
    fake_client = MagicMock()
    fake_client.get_signing_key_from_jwt.return_value = MagicMock(key=object())

    monkeypatch.setenv("CLERK_JWKS_URL", "https://test.clerk.dev/.well-known/jwks.json")
    monkeypatch.setenv("CLERK_ISSUER", "https://expected.clerk.dev")
    monkeypatch.setattr(clerk_module, "_get_jwks_client", lambda: fake_client)
    monkeypatch.setattr(
        clerk_module.jwt,
        "decode",
        lambda *args, **kwargs: (_ for _ in ()).throw(jwt.exceptions.InvalidIssuerError("wrong issuer")),
    )

    with pytest.raises(ClerkAuthError):
        verify_clerk_token("some.jwt.token")


def test_valid_payload_returned(monkeypatch):
    fake_client = MagicMock()
    fake_client.get_signing_key_from_jwt.return_value = MagicMock(key=object())

    payload = {"sub": "user_abc", "email": "user@example.com", "name": "Test User", "iss": "https://test.clerk.dev"}

    monkeypatch.setenv("CLERK_JWKS_URL", "https://test.clerk.dev/.well-known/jwks.json")
    monkeypatch.setenv("CLERK_ISSUER", "https://test.clerk.dev")
    monkeypatch.setattr(clerk_module, "_get_jwks_client", lambda: fake_client)
    monkeypatch.setattr(clerk_module.jwt, "decode", lambda *args, **kwargs: payload)

    result = verify_clerk_token("valid.jwt.token")

    assert result["sub"] == "user_abc"
    assert result["email"] == "user@example.com"
    assert result["name"] == "Test User"
