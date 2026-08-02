"""Create system users table for Angular authentication.

Revision ID: 20260802_0007
Revises: 20260728_0006
Create Date: 2026-08-02 00:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260802_0007"
down_revision: str | None = "20260728_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "system_users"
USER_ID_INDEX = "ix_system_users_user_id"
ROLE_INDEX = "ix_system_users_role"


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _index_exists(table_name: str, index_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if not _table_exists(TABLE_NAME):
        op.create_table(
            TABLE_NAME,
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.String(length=20), nullable=False),
            sa.Column("password", sa.String(length=20), nullable=False),
            sa.Column("role", sa.String(length=15), nullable=False),
            sa.Column(
                "register_date",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=True,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=True,
                server_default=sa.func.now(),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id"),
        )
    if not _index_exists(TABLE_NAME, USER_ID_INDEX):
        op.create_index(USER_ID_INDEX, TABLE_NAME, ["user_id"], unique=False)
    if not _index_exists(TABLE_NAME, ROLE_INDEX):
        op.create_index(ROLE_INDEX, TABLE_NAME, ["role"], unique=False)


def downgrade() -> None:
    if _index_exists(TABLE_NAME, ROLE_INDEX):
        op.drop_index(ROLE_INDEX, table_name=TABLE_NAME)
    if _index_exists(TABLE_NAME, USER_ID_INDEX):
        op.drop_index(USER_ID_INDEX, table_name=TABLE_NAME)
    if _table_exists(TABLE_NAME):
        op.drop_table(TABLE_NAME)
