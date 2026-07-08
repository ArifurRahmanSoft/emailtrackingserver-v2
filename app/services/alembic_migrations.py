"""Automatic Alembic migration support for EmailTrackingServer V2."""

from pathlib import Path

from alembic import command
from alembic.config import Config

from config.settings import PROJECT_ROOT


def run_pending_migrations(
    database_url: str,
    project_root: Path = PROJECT_ROOT,
) -> None:
    """Upgrade the configured Version 2 database to the latest migration head."""
    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.set_main_option(
        "script_location",
        str(project_root / "migrations"),
    )
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")
