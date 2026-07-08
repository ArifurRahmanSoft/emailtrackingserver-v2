"""Add Version 2 email metadata columns to email_tracking.

Revision ID: 20260708_0001
Revises:
Create Date: 2026-07-08 00:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260708_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "email_tracking",
        sa.Column("mail_subject", sa.String(length=998), nullable=True),
    )
    op.add_column(
        "email_tracking",
        sa.Column("project_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "email_tracking",
        sa.Column("excel_file_path", sa.Text(), nullable=True),
    )
    op.add_column(
        "email_tracking",
        sa.Column("excel_file_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "email_tracking",
        sa.Column("last_synchronize_time", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("email_tracking", "last_synchronize_time")
    op.drop_column("email_tracking", "excel_file_name")
    op.drop_column("email_tracking", "excel_file_path")
    op.drop_column("email_tracking", "project_name")
    op.drop_column("email_tracking", "mail_subject")
