"""add fuzzy verification columns to evidence tables

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-04-05

"""
from alembic import op
import sqlalchemy as sa


revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("obligation_evidence", sa.Column("verification_method", sa.String(), nullable=True))
    op.add_column("obligation_evidence", sa.Column("fuzzy_similarity", sa.Float(), nullable=True))
    op.add_column("risk_evidence", sa.Column("verification_method", sa.String(), nullable=True))
    op.add_column("risk_evidence", sa.Column("fuzzy_similarity", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("risk_evidence", "fuzzy_similarity")
    op.drop_column("risk_evidence", "verification_method")
    op.drop_column("obligation_evidence", "fuzzy_similarity")
    op.drop_column("obligation_evidence", "verification_method")
