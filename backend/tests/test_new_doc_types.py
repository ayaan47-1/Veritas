from __future__ import annotations

from backend.app.models.enums import DocumentType, ExtractionStage, ParseStatus


def test_new_real_estate_doc_types_exist():
    assert DocumentType.purchase_agreement
    assert DocumentType.title_commitment
    assert DocumentType.hoa_document
    assert DocumentType.disclosure_report


def test_new_financial_doc_types_exist():
    assert DocumentType.insurance_policy
    assert DocumentType.loan_agreement
    assert DocumentType.deed_of_trust


def test_critic_enum_values_exist():
    assert ParseStatus.critic_review
    assert ExtractionStage.critic_detection
