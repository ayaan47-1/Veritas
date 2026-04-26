from __future__ import annotations

import time
import uuid

import pytest

from backend.app.services.unsubscribe_token import (
    InvalidTokenError,
    mint_unsubscribe_token,
    verify_unsubscribe_token,
)


SECRET = "test-secret-123"


def test_round_trip_returns_same_user_id():
    user_id = uuid.uuid4()
    token = mint_unsubscribe_token(user_id, SECRET)
    assert verify_unsubscribe_token(token, SECRET) == user_id


def test_tampered_payload_raises():
    user_id = uuid.uuid4()
    token = mint_unsubscribe_token(user_id, SECRET)
    payload, sig = token.split(".", 1)
    tampered = payload[:-2] + "AA." + sig
    with pytest.raises(InvalidTokenError):
        verify_unsubscribe_token(tampered, SECRET)


def test_wrong_secret_raises():
    user_id = uuid.uuid4()
    token = mint_unsubscribe_token(user_id, SECRET)
    with pytest.raises(InvalidTokenError):
        verify_unsubscribe_token(token, "other-secret")


def test_expired_token_raises(monkeypatch):
    from backend.app.services import unsubscribe_token as ut_module

    user_id = uuid.uuid4()
    token = mint_unsubscribe_token(user_id, SECRET, ttl_days=1)
    # Pretend current time is 2 days later
    future = time.time() + 2 * 86400 + 1
    monkeypatch.setattr(ut_module.time, "time", lambda: future)
    with pytest.raises(InvalidTokenError):
        verify_unsubscribe_token(token, SECRET)


def test_malformed_token_raises():
    with pytest.raises(InvalidTokenError):
        verify_unsubscribe_token("not-a-token", SECRET)
    with pytest.raises(InvalidTokenError):
        verify_unsubscribe_token("", SECRET)


def test_empty_secret_rejected_on_mint():
    with pytest.raises(ValueError):
        mint_unsubscribe_token(uuid.uuid4(), "")


def test_empty_secret_rejected_on_verify():
    token = mint_unsubscribe_token(uuid.uuid4(), SECRET)
    with pytest.raises(InvalidTokenError):
        verify_unsubscribe_token(token, "")
