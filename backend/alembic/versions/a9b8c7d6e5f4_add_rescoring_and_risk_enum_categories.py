"""add rescoring columns and align risk type enum

Revision ID: a9b8c7d6e5f4
Revises: e1f2a3b4c5d6
Create Date: 2026-03-27 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE parsestatus ADD VALUE IF NOT EXISTS 'rescoring'")

    op.execute("ALTER TYPE risktype RENAME TO risktype_old")
    op.execute(
        "CREATE TYPE risktype AS ENUM "
        "('financial', 'schedule', 'quality', 'safety', 'compliance', 'contractual', 'unknown_risk')"
    )
    op.execute(
        """
        ALTER TABLE risks
        ALTER COLUMN risk_type TYPE risktype
        USING (
          CASE risk_type::text
            WHEN 'approval_overdue' THEN 'schedule'
            WHEN 'payment_term_conflict' THEN 'contractual'
            WHEN 'scope_change_indicator' THEN 'contractual'
            WHEN 'schedule_dependency_blocker' THEN 'schedule'
            WHEN 'missing_required_document' THEN 'compliance'
            WHEN 'expired_certificate_or_insurance' THEN 'compliance'
            WHEN 'inspection_failed_reinspection_required' THEN 'quality'
            ELSE 'unknown_risk'
          END
        )::risktype
        """
    )
    op.execute("DROP TYPE risktype_old")

    severity_enum = postgresql.ENUM(
        "low", "medium", "high", "critical", name="severity", create_type=False
    )
    op.add_column("obligations", sa.Column("llm_severity", severity_enum, nullable=True))
    op.add_column("obligations", sa.Column("llm_quality_confidence", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_obligation_llm_conf",
        "obligations",
        "llm_quality_confidence IS NULL OR (llm_quality_confidence >= 0 AND llm_quality_confidence <= 100)",
    )

    op.add_column("risks", sa.Column("llm_severity", severity_enum, nullable=True))
    op.add_column("risks", sa.Column("llm_quality_confidence", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_risk_llm_conf",
        "risks",
        "llm_quality_confidence IS NULL OR (llm_quality_confidence >= 0 AND llm_quality_confidence <= 100)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_risk_llm_conf", "risks", type_="check")
    op.drop_column("risks", "llm_quality_confidence")
    op.drop_column("risks", "llm_severity")

    op.drop_constraint("ck_obligation_llm_conf", "obligations", type_="check")
    op.drop_column("obligations", "llm_quality_confidence")
    op.drop_column("obligations", "llm_severity")

    op.execute("ALTER TYPE risktype RENAME TO risktype_new")
    op.execute(
        "CREATE TYPE risktype AS ENUM "
        "('missing_required_document', 'expired_certificate_or_insurance', "
        "'inspection_failed_reinspection_required', 'approval_overdue', "
        "'payment_term_conflict', 'scope_change_indicator', 'schedule_dependency_blocker', 'unknown_risk')"
    )
    op.execute(
        """
        ALTER TABLE risks
        ALTER COLUMN risk_type TYPE risktype
        USING (
          CASE risk_type::text
            WHEN 'financial' THEN 'approval_overdue'
            WHEN 'schedule' THEN 'schedule_dependency_blocker'
            WHEN 'quality' THEN 'inspection_failed_reinspection_required'
            WHEN 'safety' THEN 'unknown_risk'
            WHEN 'compliance' THEN 'missing_required_document'
            WHEN 'contractual' THEN 'payment_term_conflict'
            ELSE 'unknown_risk'
          END
        )::risktype
        """
    )
    op.execute("DROP TYPE risktype_new")

    op.execute("ALTER TYPE parsestatus RENAME TO parsestatus_new")
    op.execute(
        "CREATE TYPE parsestatus AS ENUM "
        "('uploaded', 'parsing', 'ocr', 'chunking', 'classification', 'extraction', "
        "'verification', 'scoring', 'complete', 'partially_processed', 'failed')"
    )
    op.execute(
        """
        ALTER TABLE documents
        ALTER COLUMN parse_status TYPE parsestatus
        USING (
          CASE parse_status::text
            WHEN 'rescoring' THEN 'scoring'
            ELSE parse_status::text
          END
        )::parsestatus
        """
    )
    op.execute("DROP TYPE parsestatus_new")
