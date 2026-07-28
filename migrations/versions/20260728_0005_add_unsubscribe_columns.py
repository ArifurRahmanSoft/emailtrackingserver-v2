"""Add unsubscribe tracking columns to email_tracking.

Revision ID: 20260728_0005
Revises: 20260713_0004
Create Date: 2026-07-28 00:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260728_0005"
down_revision: str | None = "20260713_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "email_tracking"


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if not _column_exists(table_name, column.name):
        op.add_column(table_name, column)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if _column_exists(table_name, column_name):
        op.drop_column(table_name, column_name)


def upgrade() -> None:
    _add_column_if_missing(
        TABLE_NAME,
        sa.Column(
            "unsubscribe",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    _add_column_if_missing(
        TABLE_NAME,
        sa.Column("unsubscribe_time", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    _drop_column_if_exists(TABLE_NAME, "unsubscribe_time")
    _drop_column_if_exists(TABLE_NAME, "unsubscribe")
