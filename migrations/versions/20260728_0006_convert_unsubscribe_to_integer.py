"""Convert unsubscribe from boolean to integer.

Revision ID: 20260728_0006
Revises: 20260728_0005
Create Date: 2026-07-28 00:00:01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260728_0006"
down_revision: str | None = "20260728_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "email_tracking"
COLUMN_NAME = "unsubscribe"


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _column_type(table_name: str, column_name: str) -> sa.types.TypeEngine | None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return None
    for column in inspector.get_columns(table_name):
        if column["name"] == column_name:
            return column["type"]
    return None


def upgrade() -> None:
    if not _column_exists(TABLE_NAME, COLUMN_NAME):
        return

    bind = op.get_bind()
    dialect_name = bind.dialect.name
    column_type = _column_type(TABLE_NAME, COLUMN_NAME)

    if dialect_name == "postgresql":
        if isinstance(column_type, sa.Integer):
            op.execute(
                sa.text(
                    """
                    UPDATE email_tracking
                    SET unsubscribe = 0
                    WHERE unsubscribe IS NULL
                    """
                )
            )
            op.alter_column(
                TABLE_NAME,
                COLUMN_NAME,
                existing_type=sa.Integer(),
                nullable=False,
                server_default="0",
            )
            return

        op.execute(
            sa.text(
                """
                UPDATE email_tracking
                SET unsubscribe = FALSE
                WHERE unsubscribe IS NULL
                """
            )
        )
        op.execute(
            sa.text(
                """
                ALTER TABLE email_tracking
                ALTER COLUMN unsubscribe DROP DEFAULT
                """
            )
        )
        op.execute(
            sa.text(
                """
                ALTER TABLE email_tracking
                ALTER COLUMN unsubscribe TYPE INTEGER
                USING CASE
                    WHEN unsubscribe IS TRUE THEN 1
                    ELSE 0
                END
                """
            )
        )
        op.alter_column(
            TABLE_NAME,
            COLUMN_NAME,
            existing_type=sa.Integer(),
            nullable=False,
            server_default="0",
        )
        return

    # SQLite is used by the local test suite. It stores booleans as 0/1 values
    # already and cannot change a column type without table recreation, which
    # this production migration intentionally avoids.
    op.execute(
        sa.text(
            """
            UPDATE email_tracking
            SET unsubscribe = 0
            WHERE unsubscribe IS NULL
            """
        )
    )


def downgrade() -> None:
    if not _column_exists(TABLE_NAME, COLUMN_NAME):
        return

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        sa.text(
            """
            ALTER TABLE email_tracking
            ALTER COLUMN unsubscribe TYPE BOOLEAN
            USING CASE
                WHEN unsubscribe = 1 THEN TRUE
                ELSE FALSE
            END
            """
        )
    )
    op.alter_column(
        TABLE_NAME,
        COLUMN_NAME,
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.false(),
    )
