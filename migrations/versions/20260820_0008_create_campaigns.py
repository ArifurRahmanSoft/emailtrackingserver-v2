"""Create independent campaigns table.

Revision ID: 20260820_0008
Revises: 20260802_0007
Create Date: 2026-08-20 00:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260820_0008"
down_revision: str | None = "20260802_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "campaigns"


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    """Create only the independent campaigns table."""
    if _table_exists(TABLE_NAME):
        return

    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_name", sa.String(length=255), nullable=False),
        sa.Column("campaign_code", sa.String(length=100), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("file_name", sa.String(length=500), nullable=True),
        sa.Column("client_name", sa.String(length=255), nullable=True),
        sa.Column("campaign_offer", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_code"),
    )


def downgrade() -> None:
    """Remove only the independent campaigns table."""
    if _table_exists(TABLE_NAME):
        op.drop_table(TABLE_NAME)
