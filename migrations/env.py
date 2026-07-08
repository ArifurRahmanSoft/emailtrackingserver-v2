"""Alembic environment for EmailTrackingServer V2."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.models.attachment import AttachmentBase
from app.models.email_tracking import Base
from config.settings import load_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = [Base.metadata, AttachmentBase.metadata]


def _database_url() -> str:
    """Return the Version 2 database URL from Alembic config or environment."""
    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url:
        return _normalize_database_url(configured_url)

    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required to run Alembic migrations.")
    return _normalize_database_url(settings.database_url)


def _normalize_database_url(database_url: str) -> str:
    """Use psycopg 3 for normal PostgreSQL URLs."""
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def run_migrations_offline() -> None:
    """Run migrations without creating an Engine."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations through a live database connection."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
