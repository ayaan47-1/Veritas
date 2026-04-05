"""scope document sha256 unique constraint to asset

Revision ID: c4d5e6f7a8b9
Revises: b2e4f6a8c0d1
Create Date: 2026-04-05

"""
from alembic import op

revision = "c4d5e6f7a8b9"
down_revision = "b2e4f6a8c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("documents_sha256_key", "documents", type_="unique")
    op.create_unique_constraint("uq_document_sha256_asset", "documents", ["sha256", "asset_id"])


def downgrade() -> None:
    op.drop_constraint("uq_document_sha256_asset", "documents", type_="unique")
    op.create_unique_constraint("documents_sha256_key", "documents", ["sha256"])
