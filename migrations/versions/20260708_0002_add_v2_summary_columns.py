"""Add Version 2 sender and summary columns to email_tracking.

Revision ID: 20260708_0002
Revises: 20260708_0001
Create Date: 2026-07-08 00:00:01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260708_0002"
down_revision: str | None = "20260708_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
        "email_tracking",
        sa.Column("sender_mail", sa.String(length=320), nullable=True),
    )
    _add_column_if_missing(
        "email_tracking",
        sa.Column("mail_subject", sa.String(length=998), nullable=True),
    )
    _add_column_if_missing(
        "email_tracking",
        sa.Column("project_name", sa.String(length=255), nullable=True),
    )
    _add_column_if_missing(
        "email_tracking",
        sa.Column("excel_file_path", sa.Text(), nullable=True),
    )
    _add_column_if_missing(
        "email_tracking",
        sa.Column("excel_file_name", sa.String(length=255), nullable=True),
    )
    _add_column_if_missing(
        "email_tracking",
        sa.Column("last_synchronize_time", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column_if_missing(
        "email_tracking",
        sa.Column("download_count", sa.Integer(), nullable=True, server_default="0"),
    )
    _add_column_if_missing(
        "email_tracking",
        sa.Column("first_download", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column_if_missing(
        "email_tracking",
        sa.Column("last_download", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column_if_missing(
        "email_tracking",
        sa.Column("reply_count", sa.Integer(), nullable=True, server_default="0"),
    )
    _add_column_if_missing(
        "email_tracking",
        sa.Column("first_reply", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column_if_missing(
        "email_tracking",
        sa.Column("last_reply", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    _drop_column_if_exists("email_tracking", "last_reply")
    _drop_column_if_exists("email_tracking", "first_reply")
    _drop_column_if_exists("email_tracking", "reply_count")
    _drop_column_if_exists("email_tracking", "last_download")
    _drop_column_if_exists("email_tracking", "first_download")
    _drop_column_if_exists("email_tracking", "download_count")
    _drop_column_if_exists("email_tracking", "last_synchronize_time")
    _drop_column_if_exists("email_tracking", "excel_file_name")
    _drop_column_if_exists("email_tracking", "excel_file_path")
    _drop_column_if_exists("email_tracking", "project_name")
    _drop_column_if_exists("email_tracking", "mail_subject")
    _drop_column_if_exists("email_tracking", "sender_mail")
