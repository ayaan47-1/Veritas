from __future__ import annotations

import logging
import os
from uuid import UUID

import inngest

from ..config import settings
from ..database import SessionLocal
from ..services.email import EmailSendError, send_email
from .inngest_client import inngest_client
from .tasks.digest import (
    build_unsubscribe_url,
    compose_user_digest,
    load_digest_enabled_user_ids,
)


logger = logging.getLogger(__name__)


def _digest_config() -> dict:
    raw = settings.raw.get("digest", {}) or {}
    return {
        "from_address": raw.get("from_address", "digest@veritaslayer.net"),
        "public_base_url": raw.get("public_base_url", "https://veritaslayer.net"),
        "enabled": raw.get("enabled", True),
    }


@inngest_client.create_function(
    fn_id="weekly-digest-dispatch",
    trigger=inngest.TriggerCron(cron="0 12 * * 1"),  # Mondays 12:00 UTC (7am CT / 8am ET)
)
async def weekly_digest_dispatch(
    ctx: inngest.Context,
    step: inngest.Step,
) -> dict:
    cfg = _digest_config()
    if not cfg.get("enabled", True):
        return {"dispatched": 0, "skipped_reason": "digest.enabled=false"}

    user_ids: list[str] = await step.run("load-users", load_digest_enabled_user_ids)
    if not user_ids:
        return {"dispatched": 0}

    await step.send_event(
        "fanout",
        [
            inngest.Event(
                name="veritas/digest.send",
                data={"user_id": uid},
            )
            for uid in user_ids
        ],
    )
    return {"dispatched": len(user_ids)}


def _run_send(user_id_text: str) -> dict:
    cfg = _digest_config()
    secret = os.getenv("DIGEST_UNSUBSCRIBE_SECRET", "").strip()

    db = SessionLocal()
    try:
        user_id = UUID(user_id_text)
        payload = compose_user_digest(db, user_id)
    finally:
        db.close()

    if payload is None:
        return {"user_id": user_id_text, "status": "skipped_empty"}

    if not secret:
        logger.warning(
            "send_user_digest skipped: DIGEST_UNSUBSCRIBE_SECRET not set"
        )
        return {"user_id": user_id_text, "status": "skipped_no_secret"}

    # Swap the placeholder unsubscribe URL for a per-user signed link.
    real_unsubscribe = build_unsubscribe_url(
        user_id, secret, cfg["public_base_url"]
    )
    html = payload["html"].replace(
        f"{cfg['public_base_url']}/unsubscribe/pending",
        real_unsubscribe,
    )

    try:
        result = send_email(
            to=payload["recipient"],
            subject=payload["subject"],
            html=html,
            from_address=cfg["from_address"],
            list_unsubscribe=real_unsubscribe,
        )
    except EmailSendError:
        logger.exception("digest send failed for user %s", user_id_text)
        raise

    return {
        "user_id": user_id_text,
        "status": "sent",
        "message_id": result.message_id,
        "item_count": payload["item_count"],
        "critical_count": payload["critical_count"],
    }


@inngest_client.create_function(
    fn_id="send-user-digest",
    trigger=inngest.TriggerEvent(event="veritas/digest.send"),
    retries=3,
)
async def send_user_digest(
    ctx: inngest.Context,
    step: inngest.Step,
) -> dict:
    user_id_text = str(ctx.event.data["user_id"])
    return await step.run(
        "compose-and-send", lambda: _run_send(user_id_text)
    )
