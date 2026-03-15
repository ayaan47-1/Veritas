"""add clerk oidc provider

Revision ID: e1f2a3b4c5d6
Revises: c03dec85f67a
Create Date: 2026-03-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'c03dec85f67a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE oidcprovider ADD VALUE IF NOT EXISTS 'clerk'")


def downgrade() -> None:
    # Postgres does not support removing enum values; downgrade is a no-op.
    pass
