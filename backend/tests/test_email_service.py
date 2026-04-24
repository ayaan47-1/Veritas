from __future__ import annotations

import io
import json
from urllib.error import HTTPError

import pytest

from backend.app.services import email as email_module
from backend.app.services.email import EmailSendError, send_email


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _make_urlopen(body: dict, captured: list):
    def _urlopen(req, timeout=None):  # noqa: ARG001
        captured.append(req)
        return FakeResponse(json.dumps(body).encode("utf-8"))

    return _urlopen


def test_send_email_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(EmailSendError, match="RESEND_API_KEY"):
        send_email(to="a@b.com", subject="s", html="<p>x</p>", from_address="x@y.com")


def test_send_email_posts_expected_payload(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    captured: list = []
    monkeypatch.setattr(
        email_module.request,
        "urlopen",
        _make_urlopen({"id": "abc-123"}, captured),
    )

    result = send_email(
        to="user@example.com",
        subject="hello",
        html="<p>body</p>",
        from_address="digest@veritaslayer.net",
    )

    assert result.message_id == "abc-123"
    assert len(captured) == 1
    req = captured[0]
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["from"] == "digest@veritaslayer.net"
    assert payload["to"] == ["user@example.com"]
    assert payload["subject"] == "hello"
    assert payload["html"] == "<p>body</p>"
    assert "headers" not in payload
    assert req.headers["Authorization"] == "Bearer test-key"
    assert req.headers["Content-type"] == "application/json"


def test_send_email_sets_one_click_unsubscribe_headers(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    captured: list = []
    monkeypatch.setattr(
        email_module.request,
        "urlopen",
        _make_urlopen({"id": "xyz"}, captured),
    )

    send_email(
        to="user@example.com",
        subject="s",
        html="<p>x</p>",
        from_address="digest@veritaslayer.net",
        list_unsubscribe="https://api.example.com/users/unsubscribe/TOKEN",
    )

    payload = json.loads(captured[0].data.decode("utf-8"))
    assert payload["headers"]["List-Unsubscribe"] == "<https://api.example.com/users/unsubscribe/TOKEN>"
    assert payload["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_send_email_wraps_http_error(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key")

    def _raise(req, timeout=None):  # noqa: ARG001
        raise HTTPError(
            url="https://api.resend.com/emails",
            code=422,
            msg="Unprocessable",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b'{"error":"invalid"}'),
        )

    monkeypatch.setattr(email_module.request, "urlopen", _raise)

    with pytest.raises(EmailSendError, match="422"):
        send_email(to="a@b.com", subject="s", html="<p>x</p>", from_address="x@y.com")


def test_send_email_rejects_empty_recipient(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    with pytest.raises(EmailSendError, match="recipient"):
        send_email(to="", subject="s", html="<p>x</p>", from_address="x@y.com")


def test_send_email_rejects_response_without_id(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    captured: list = []
    monkeypatch.setattr(
        email_module.request,
        "urlopen",
        _make_urlopen({"no_id": True}, captured),
    )
    with pytest.raises(EmailSendError, match="missing id"):
        send_email(to="a@b.com", subject="s", html="<p>x</p>", from_address="x@y.com")
