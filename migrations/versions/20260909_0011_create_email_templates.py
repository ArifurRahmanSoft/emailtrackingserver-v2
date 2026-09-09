"""Create independent email_templates table."""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "20260909_0011"
down_revision: str | None = "20260831_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "email_templates" in inspector.get_table_names():
        return
    op.create_table(
        "email_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_name", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("signature_html", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "email_templates" in inspector.get_table_names():
        op.drop_table("email_templates")
