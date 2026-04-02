"""add ifc_models, compliance_reports, and compliance_results tables

Revision ID: b2e4f6a8c0d1
Revises: f3c7beac04b9
Create Date: 2026-04-01 20:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2e4f6a8c0d1"
down_revision: Union[str, None] = "7c1d4e2b9a10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ifc_models
    op.create_table(
        "ifc_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String, nullable=False),
        sa.Column("file_path", sa.String, nullable=False),
        sa.Column("sha256", sa.String, nullable=False, unique=True),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "parse_status",
            sa.Enum("uploaded", "processing", "processed", "failed", name="ifcparsestatus"),
            nullable=False,
            server_default="uploaded",
        ),
        sa.Column("element_count", sa.Integer, nullable=True),
        sa.Column("element_types", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_ifc_models_sha256", "ifc_models", ["sha256"], unique=True)

    # compliance_reports
    op.create_table(
        "compliance_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "ifc_model_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ifc_models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "spec_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "running", "completed", "failed", name="reportstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("total", sa.Integer, nullable=True),
        sa.Column("passed", sa.Integer, nullable=True),
        sa.Column("failed", sa.Integer, nullable=True),
        sa.Column("warnings", sa.Integer, nullable=True),
        sa.Column("not_applicable", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_compliance_reports_ifc_model_id", "compliance_reports", ["ifc_model_id"])
    op.create_index("ix_compliance_reports_spec_document_id", "compliance_reports", ["spec_document_id"])
    op.create_index("ix_compliance_reports_status", "compliance_reports", ["status"])

    # compliance_results
    op.create_table(
        "compliance_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "report_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("compliance_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_id", sa.String, nullable=False),
        sa.Column("section", sa.String, nullable=False),
        sa.Column("requirement", sa.Text, nullable=False),
        sa.Column("element_express_id", sa.Integer, nullable=True),
        sa.Column("element_type", sa.String, nullable=True),
        sa.Column("element_name", sa.String, nullable=True),
        sa.Column(
            "status",
            sa.Enum("pass", "fail", "warning", "not_applicable", name="resultstatus"),
            nullable=False,
        ),
        sa.Column("actual_value", sa.String, nullable=True),
        sa.Column("message", sa.Text, nullable=False),
    )
    op.create_index(
        "ix_compliance_results_report_status",
        "compliance_results",
        ["report_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("compliance_results")
    op.drop_table("compliance_reports")
    op.drop_table("ifc_models")
    op.execute("DROP TYPE resultstatus")
    op.execute("DROP TYPE reportstatus")
    op.execute("DROP TYPE ifcparsestatus")
