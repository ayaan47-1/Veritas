"""add lease to document_type enum

Revision ID: f3c7beac04b9
Revises: a9b8c7d6e5f4
Create Date: 2026-03-29 13:44:45.097688

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f3c7beac04b9'
down_revision: Union[str, None] = 'a9b8c7d6e5f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE documenttype ADD VALUE IF NOT EXISTS 'lease' AFTER 'contract'")


def downgrade() -> None:
    # Postgres does not support removing enum values; downgrade is a no-op.
    pass
