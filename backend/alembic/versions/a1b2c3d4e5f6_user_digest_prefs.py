"""add digest preferences to users

Revision ID: a1b2c3d4e5f6
Revises: f7a8b9c0d1e2
Create Date: 2026-04-23

"""
from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("digest_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "users",
        sa.Column(
            "digest_timezone",
            sa.String(length=64),
            nullable=False,
            server_default="America/Chicago",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "digest_timezone")
    op.drop_column("users", "digest_enabled")
