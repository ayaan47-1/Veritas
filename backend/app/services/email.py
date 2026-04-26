from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib import request
from urllib.error import HTTPError, URLError


class EmailSendError(RuntimeError):
    pass


RESEND_ENDPOINT = "https://api.resend.com/emails"


@dataclass(frozen=True)
class EmailResult:
    message_id: str


def send_email(
    *,
    to: str,
    subject: str,
    html: str,
    from_address: str,
    list_unsubscribe: str | None = None,
    timeout: float = 15.0,
) -> EmailResult:
    """Send a single HTML email via the Resend REST API.

    Raises EmailSendError for missing config, transport errors, or non-2xx responses.
    """
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        raise EmailSendError("RESEND_API_KEY is not configured")
    if not to:
        raise EmailSendError("recipient address is required")
    if not from_address:
        raise EmailSendError("from_address is required")

    payload: dict[str, object] = {
        "from": from_address,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    # Gmail/Yahoo bulk-sender rule: List-Unsubscribe + one-click POST header.
    if list_unsubscribe:
        payload["headers"] = {
            "List-Unsubscribe": f"<{list_unsubscribe}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }

    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        RESEND_ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed Resend URL
            response_body = resp.read().decode("utf-8")
            parsed = json.loads(response_body) if response_body else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise EmailSendError(f"Resend HTTP error {exc.code}: {detail}") from exc
    except URLError as exc:
        raise EmailSendError(f"Resend unreachable: {exc.reason}") from exc

    message_id = parsed.get("id") if isinstance(parsed, dict) else None
    if not message_id:
        raise EmailSendError(f"Resend response missing id: {parsed!r}")

    return EmailResult(message_id=str(message_id))
