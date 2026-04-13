"""add critic stage enums and critic model fields

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-04-12

"""
from alembic import op
import sqlalchemy as sa


revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE parsestatus ADD VALUE IF NOT EXISTS 'critic_review'")
    op.execute("ALTER TYPE extractionstage ADD VALUE IF NOT EXISTS 'critic_detection'")

    op.add_column("obligations", sa.Column("critic_valid", sa.Boolean(), nullable=True))
    op.add_column("obligations", sa.Column("critic_confidence", sa.Integer(), nullable=True))
    op.add_column("obligations", sa.Column("critic_reasoning", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_obligation_critic_conf",
        "obligations",
        "critic_confidence IS NULL OR (critic_confidence >= 0 AND critic_confidence <= 100)",
    )

    op.add_column("risks", sa.Column("critic_valid", sa.Boolean(), nullable=True))
    op.add_column("risks", sa.Column("critic_confidence", sa.Integer(), nullable=True))
    op.add_column("risks", sa.Column("critic_reasoning", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_risk_critic_conf",
        "risks",
        "critic_confidence IS NULL OR (critic_confidence >= 0 AND critic_confidence <= 100)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_risk_critic_conf", "risks", type_="check")
    op.drop_column("risks", "critic_reasoning")
    op.drop_column("risks", "critic_confidence")
    op.drop_column("risks", "critic_valid")

    op.drop_constraint("ck_obligation_critic_conf", "obligations", type_="check")
    op.drop_column("obligations", "critic_reasoning")
    op.drop_column("obligations", "critic_confidence")
    op.drop_column("obligations", "critic_valid")

    op.execute("ALTER TYPE extractionstage RENAME TO extractionstage_new")
    op.execute(
        "CREATE TYPE extractionstage AS ENUM "
        "('classification', 'entity_extraction', 'obligation_extraction', 'risk_extraction')"
    )
    op.execute(
        """
        ALTER TABLE extraction_runs
        ALTER COLUMN stage TYPE extractionstage
        USING (
          CASE stage::text
            WHEN 'critic_detection' THEN 'risk_extraction'
            ELSE stage::text
          END
        )::extractionstage
        """
    )
    op.execute("DROP TYPE extractionstage_new")

    op.execute("ALTER TYPE parsestatus RENAME TO parsestatus_new")
    op.execute(
        "CREATE TYPE parsestatus AS ENUM "
        "('uploaded', 'parsing', 'ocr', 'chunking', 'classification', 'extraction', "
        "'verification', 'scoring', 'rescoring', 'complete', 'partially_processed', 'failed')"
    )
    op.execute(
        """
        ALTER TABLE documents
        ALTER COLUMN parse_status TYPE parsestatus
        USING (
          CASE parse_status::text
            WHEN 'critic_review' THEN 'verification'
            ELSE parse_status::text
          END
        )::parsestatus
        """
    )
    op.execute("DROP TYPE parsestatus_new")
