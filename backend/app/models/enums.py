from __future__ import annotations

import enum


class UserRole(str, enum.Enum):
    admin = "admin"
    reviewer = "reviewer"
    viewer = "viewer"


class OIDCProvider(str, enum.Enum):
    google = "google"
    microsoft = "microsoft"


class DocumentType(str, enum.Enum):
    contract = "contract"
    inspection_report = "inspection_report"
    rfi = "rfi"
    change_order = "change_order"
    invoice = "invoice"
    unknown = "unknown"


class ParseStatus(str, enum.Enum):
    uploaded = "uploaded"
    parsing = "parsing"
    ocr = "ocr"
    chunking = "chunking"
    classification = "classification"
    extraction = "extraction"
    verification = "verification"
    scoring = "scoring"
    complete = "complete"
    partially_processed = "partially_processed"
    failed = "failed"


class TextSource(str, enum.Enum):
    pdf_text = "pdf_text"
    ocr = "ocr"


class PageProcessingStatus(str, enum.Enum):
    pending = "pending"
    processed = "processed"
    failed = "failed"


class SplitReason(str, enum.Enum):
    full_page = "full_page"
    section_split = "section_split"
    token_limit = "token_limit"


class EntityType(str, enum.Enum):
    party = "party"
    person = "person"
    org = "org"
    location = "location"
    system = "system"
    other = "other"


class ExtractionStage(str, enum.Enum):
    classification = "classification"
    entity_extraction = "entity_extraction"
    obligation_extraction = "obligation_extraction"
    risk_extraction = "risk_extraction"


class ExtractionStatus(str, enum.Enum):
    running = "running"
    completed = "completed"
    failed = "failed"
    superseded = "superseded"


class ObligationType(str, enum.Enum):
    compliance = "compliance"
    submission = "submission"
    payment = "payment"
    inspection = "inspection"
    notification = "notification"
    other = "other"


class Modality(str, enum.Enum):
    must = "must"
    shall = "shall"
    required = "required"
    should = "should"
    may = "may"
    unknown = "unknown"


class DueKind(str, enum.Enum):
    absolute = "absolute"
    relative = "relative"
    resolved_relative = "resolved_relative"
    none = "none"


class Severity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ReviewStatus(str, enum.Enum):
    needs_review = "needs_review"
    confirmed = "confirmed"
    rejected = "rejected"


class RiskType(str, enum.Enum):
    missing_required_document = "missing_required_document"
    expired_certificate_or_insurance = "expired_certificate_or_insurance"
    inspection_failed_reinspection_required = "inspection_failed_reinspection_required"
    approval_overdue = "approval_overdue"
    payment_term_conflict = "payment_term_conflict"
    scope_change_indicator = "scope_change_indicator"
    schedule_dependency_blocker = "schedule_dependency_blocker"
    unknown_risk = "unknown_risk"


class ReviewDecision(str, enum.Enum):
    approve = "approve"
    reject = "reject"
    edit_approve = "edit_approve"


class AuditAction(str, enum.Enum):
    create = "create"
    update = "update"
    delete = "delete"


class NotificationEventType(str, enum.Enum):
    processing_complete = "processing_complete"
    deadline_approaching = "deadline_approaching"
    items_awaiting_review = "items_awaiting_review"
    risk_detected = "risk_detected"
    weekly_summary_ready = "weekly_summary_ready"


class NotificationChannel(str, enum.Enum):
    in_app = "in_app"
    email = "email"


class NotificationStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    read = "read"

