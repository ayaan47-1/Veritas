from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from ...config import settings
from ...database import SessionLocal
from ...models import (
    Asset,
    Document,
    Obligation,
    ReviewStatus,
    Severity,
    User,
    UserAssetAssignment,
)
from ...services.unsubscribe_token import mint_unsubscribe_token


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.critical: 3,
    Severity.high: 2,
    Severity.medium: 1,
    Severity.low: 0,
}

_SEVERITY_COLORS: dict[Severity, str] = {
    Severity.critical: "#dc2626",
    Severity.high: "#ea580c",
    Severity.medium: "#ca8a04",
    Severity.low: "#2563eb",
}

_BUCKET_TITLES = {
    "critical_this_week": "Critical this week",
    "due_next_14_days": "Due in the next 14 days",
    "coming_up_30_days": "Coming up in 30 days",
}


@dataclass(frozen=True)
class DigestItem:
    obligation_id: UUID
    asset_name: str
    text: str
    due_date: date
    severity: Severity


def _truncate_text(text: str, limit: int = 120) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit + 1]
    pivot = cut.rfind(" ")
    if pivot <= 40:
        pivot = limit
    return text[:pivot].rstrip() + "…"


def _digest_config() -> dict:
    raw = settings.raw.get("digest", {}) or {}
    return {
        "from_address": raw.get("from_address", "digest@veritaslayer.net"),
        "public_base_url": raw.get("public_base_url", "https://veritaslayer.net"),
        "enabled": raw.get("enabled", True),
    }


def _bucket_for(
    item: DigestItem, today: date
) -> str:
    days = (item.due_date - today).days
    if days <= 7 and item.severity in {Severity.critical, Severity.high}:
        return "critical_this_week"
    if days <= 14:
        return "due_next_14_days"
    return "coming_up_30_days"


def _sort_items(items: Iterable[DigestItem]) -> list[DigestItem]:
    # Ascending by due_date, then descending severity rank.
    return sorted(
        items,
        key=lambda it: (it.due_date, -_SEVERITY_RANK.get(it.severity, 0)),
    )


def compose_user_digest(
    db: Session, user_id: UUID, today: date | None = None
) -> dict | None:
    """Build a weekly digest payload for a single user.

    Returns None when there is nothing to send (empty result set).
    """
    today = today or date.today()
    window_end = today + timedelta(days=30)

    assignments = (
        db.query(UserAssetAssignment)
        .filter(UserAssetAssignment.user_id == user_id)
        .all()
    )
    asset_ids = {a.asset_id for a in assignments}
    if not asset_ids:
        return None

    # Fetch broadly by a single equality predicate, then filter in Python.
    # This is deliberate: keeps the ORM surface small (single `==` predicate
    # per query) so the FakeSession unit tests don't need SQLAlchemy operator
    # introspection, while still hitting existing indexes on asset_id /
    # document_id. For tens-of-thousands-of-docs scale this is fine — each
    # user sees only their assigned subset.
    all_documents: list[Document] = []
    for asset_id in asset_ids:
        all_documents.extend(
            db.query(Document).filter(Document.asset_id == asset_id).all()
        )
    doc_to_asset: dict = {d.id: d.asset_id for d in all_documents}
    document_ids = set(doc_to_asset.keys())
    if not document_ids:
        return None

    obligations: list[Obligation] = []
    for doc_id in document_ids:
        obligations.extend(
            db.query(Obligation).filter(Obligation.document_id == doc_id).all()
        )

    allowed_status = {ReviewStatus.confirmed, ReviewStatus.needs_review}
    asset_name_by_id: dict = {}
    for asset_id in asset_ids:
        asset = db.query(Asset).filter(Asset.id == asset_id).first()
        if asset is not None:
            asset_name_by_id[asset.id] = asset.name

    items: list[DigestItem] = []
    for ob in obligations:
        if ob.due_date is None:
            continue
        if ob.due_date < today or ob.due_date > window_end:
            continue
        if ob.status not in allowed_status:
            continue
        asset_id = doc_to_asset.get(ob.document_id)
        if asset_id is None:
            continue
        items.append(
            DigestItem(
                obligation_id=ob.id,
                asset_name=asset_name_by_id.get(asset_id, "(unknown asset)"),
                text=ob.obligation_text,
                due_date=ob.due_date,
                severity=ob.severity,
            )
        )

    if not items:
        return None

    buckets: dict[str, list[DigestItem]] = {
        "critical_this_week": [],
        "due_next_14_days": [],
        "coming_up_30_days": [],
    }
    for it in items:
        buckets[_bucket_for(it, today)].append(it)
    for name in buckets:
        buckets[name] = _sort_items(buckets[name])

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.email:
        return None

    cfg = _digest_config()
    total = sum(len(b) for b in buckets.values())
    critical_count = len(buckets["critical_this_week"])

    if critical_count > 0:
        subject = f"{critical_count} critical obligations due this week"
    else:
        subject = (
            f"Your VeritasLayer weekly digest — {total} obligations approaching"
        )

    # Composition doesn't know the live unsubscribe secret; the Inngest
    # `send_user_digest` function patches the URL into the HTML just before
    # dispatch. Here we emit a placeholder so the template is self-contained
    # and easy to unit-test.
    unsubscribe_url = f"{cfg['public_base_url']}/unsubscribe/pending"

    html_body = render_digest_html(
        buckets=buckets,
        user=user,
        unsubscribe_url=unsubscribe_url,
        base_url=cfg["public_base_url"],
    )

    return {
        "subject": subject,
        "html": html_body,
        "recipient": user.email,
        "item_count": total,
        "critical_count": critical_count,
    }


def _render_row(item: DigestItem, base_url: str) -> str:
    color = _SEVERITY_COLORS.get(item.severity, "#6b7280")
    pill = (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:9999px;'
        f'background:{color};color:#ffffff;font-size:11px;font-weight:600;'
        f'text-transform:uppercase;letter-spacing:0.03em;">'
        f"{html.escape(item.severity.value)}</span>"
    )
    link = f"{base_url}/obligations/{item.obligation_id}"
    due_text = item.due_date.strftime("%a, %b %-d")
    return (
        '<tr><td style="padding:12px 0;border-bottom:1px solid #e5e7eb;">'
        f'<div style="margin-bottom:4px;">{pill} '
        f'<span style="color:#374151;font-size:13px;">'
        f"{html.escape(item.asset_name)}</span></div>"
        f'<div style="color:#111827;font-size:14px;line-height:1.45;'
        'margin-bottom:4px;">'
        f"{html.escape(_truncate_text(item.text))}</div>"
        f'<div style="color:#6b7280;font-size:12px;">'
        f"Due {html.escape(due_text)} &nbsp;·&nbsp; "
        f'<a href="{html.escape(link)}" '
        f'style="color:#2563eb;text-decoration:none;">View details →</a>'
        "</div></td></tr>"
    )


def render_digest_html(
    *,
    buckets: dict[str, list[DigestItem]],
    user: User,
    unsubscribe_url: str,
    base_url: str,
) -> str:
    sections: list[str] = []
    for key in ("critical_this_week", "due_next_14_days", "coming_up_30_days"):
        items = buckets.get(key) or []
        if not items:
            continue
        rows = "".join(_render_row(it, base_url) for it in items)
        sections.append(
            f'<h2 style="font-size:15px;font-weight:600;color:#111827;'
            f'margin:24px 0 8px 0;">{html.escape(_BUCKET_TITLES[key])} '
            f'<span style="color:#6b7280;font-weight:400;">({len(items)})</span>'
            f"</h2>"
            f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'width="100%" style="border-collapse:collapse;">{rows}</table>'
        )

    greeting_name = html.escape((user.name or user.email or "there").split("@")[0])
    body = "".join(sections)
    return (
        '<!doctype html><html><body style="margin:0;padding:0;'
        'background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'
        '\"Segoe UI\",Roboto,Helvetica,Arial,sans-serif;">'
        '<div style="max-width:600px;margin:0 auto;padding:24px 16px;'
        'background:#ffffff;">'
        f'<h1 style="font-size:18px;font-weight:700;color:#111827;margin:0 0 16px 0;">'
        f"VeritasLayer weekly digest</h1>"
        f'<p style="color:#374151;font-size:14px;line-height:1.5;margin:0 0 8px 0;">'
        f"Hi {greeting_name}, here are the obligations approaching their deadlines "
        "on the assets you follow.</p>"
        f"{body}"
        '<hr style="border:none;border-top:1px solid #e5e7eb;margin:28px 0 16px 0;"/>'
        '<p style="color:#9ca3af;font-size:11px;line-height:1.5;margin:0;">'
        "You're receiving this because you have email digests enabled. "
        f'<a href="{html.escape(unsubscribe_url)}" '
        'style="color:#6b7280;">Unsubscribe</a>.'
        "</p></div></body></html>"
    )


# Used by the Inngest send_user_digest function — kept thin so the Inngest
# layer can be exercised independently of composition.
def load_digest_enabled_user_ids() -> list[str]:
    db = SessionLocal()
    try:
        rows = (
            db.query(User.id)
            .filter(User.is_active.is_(True), User.digest_enabled.is_(True))
            .all()
        )
        return [str(r[0]) for r in rows]
    finally:
        db.close()


def build_unsubscribe_url(user_id: UUID, secret: str, base_url: str) -> str:
    token = mint_unsubscribe_token(user_id, secret)
    return f"{base_url.rstrip('/')}/users/unsubscribe/{token}"
