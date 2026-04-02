"""add domain column and multidomain doc types

Revision ID: 7c1d4e2b9a10
Revises: f3c7beac04b9
Create Date: 2026-03-31 20:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "7c1d4e2b9a10"
down_revision: Union[str, None] = "f3c7beac04b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE documenttype ADD VALUE IF NOT EXISTS 'purchase_agreement'")
    op.execute("ALTER TYPE documenttype ADD VALUE IF NOT EXISTS 'title_commitment'")
    op.execute("ALTER TYPE documenttype ADD VALUE IF NOT EXISTS 'hoa_document'")
    op.execute("ALTER TYPE documenttype ADD VALUE IF NOT EXISTS 'disclosure_report'")
    op.execute("ALTER TYPE documenttype ADD VALUE IF NOT EXISTS 'insurance_policy'")
    op.execute("ALTER TYPE documenttype ADD VALUE IF NOT EXISTS 'loan_agreement'")
    op.execute("ALTER TYPE documenttype ADD VALUE IF NOT EXISTS 'deed_of_trust'")
    op.add_column("documents", sa.Column("domain", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "domain")
    # Postgres does not support removing enum values.

