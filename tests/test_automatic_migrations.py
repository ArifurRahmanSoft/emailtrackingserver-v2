"""Tests for automatic Version 2 Alembic migration support."""

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

import app.services.database_tracking as database_tracking_module
from app.models.email_tracking import Base, EmailTracking
from app.services.alembic_migrations import run_pending_migrations
from app.services.database_tracking import DatabaseTrackingService


REQUIRED_V2_COLUMNS = {
    "sender_mail",
    "mail_subject",
    "project_name",
    "excel_file_path",
    "excel_file_name",
    "last_synchronize_time",
    "download_count",
    "first_download",
    "last_download",
    "reply_count",
    "first_reply",
    "last_reply",
    "message_id",
}


def _create_legacy_email_tracking_table(database_path: Path) -> str:
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE email_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking_id VARCHAR(128) NOT NULL UNIQUE,
                    recipient_email VARCHAR(320),
                    sender_email VARCHAR(320),
                    open_count INTEGER NOT NULL DEFAULT 0,
                    click_count INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO email_tracking (
                    tracking_id,
                    sender_email,
                    recipient_email,
                    open_count,
                    click_count
                )
                VALUES (
                    'legacy-v1-compatible',
                    'sender@example.com',
                    'recipient@example.com',
                    2,
                    1
                )
                """
            )
        )
    engine.dispose()
    return database_url


def test_database_initialize_executes_pending_migrations(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    calls: list[str] = []

    def fake_run_pending_migrations(database_url: str) -> None:
        calls.append(database_url)

    monkeypatch.setattr(
        database_tracking_module,
        "run_pending_migrations",
        fake_run_pending_migrations,
    )

    service = DatabaseTrackingService(None)
    service._engine = engine
    service._database_url = "sqlite+pysqlite:///:memory:"
    service._session_factory = session_factory

    service.initialize()

    assert calls == ["sqlite+pysqlite:///:memory:"]


def test_missing_v2_columns_are_created_by_migrations(tmp_path: Path) -> None:
    database_url = _create_legacy_email_tracking_table(tmp_path / "legacy.db")

    run_pending_migrations(database_url)

    engine = create_engine(database_url)
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("email_tracking")}
    indexes = {index["name"] for index in inspector.get_indexes("email_tracking")}
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT
                    download_count,
                    first_download,
                    last_download,
                    reply_count,
                    first_reply,
                    last_reply
                FROM email_tracking
                WHERE tracking_id = 'legacy-v1-compatible'
                """
            )
        ).mappings().one()
    engine.dispose()

    assert REQUIRED_V2_COLUMNS.issubset(columns)
    assert "ix_email_tracking_message_id" in indexes
    assert row["download_count"] == 0
    assert row["first_download"] is None
    assert row["last_download"] is None
    assert row["reply_count"] == 0
    assert row["first_reply"] is None
    assert row["last_reply"] is None


def test_migrations_are_idempotent_for_existing_databases(tmp_path: Path) -> None:
    database_url = _create_legacy_email_tracking_table(tmp_path / "existing.db")

    run_pending_migrations(database_url)
    run_pending_migrations(database_url)

    engine = create_engine(database_url)
    columns = {column["name"] for column in inspect(engine).get_columns("email_tracking")}
    engine.dispose()

    assert REQUIRED_V2_COLUMNS.issubset(columns)


def test_legacy_tracking_rows_still_work_with_new_nullable_columns() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as session:
        session.add(
            EmailTracking(
                tracking_id="legacy-v1-compatible",
                sender_email="sender@example.com",
                recipient_email="recipient@example.com",
                open_count=1,
                click_count=0,
            )
        )
        session.commit()

    with session_factory() as session:
        record = session.query(EmailTracking).filter_by(
            tracking_id="legacy-v1-compatible"
        ).one()

    assert record.sender_email == "sender@example.com"
    assert record.sender_mail is None
    assert record.mail_subject is None
    assert record.project_name is None
    assert record.excel_file_path is None
    assert record.excel_file_name is None
    assert record.last_synchronize_time is None
    assert record.download_count == 0
    assert record.first_download is None
    assert record.last_download is None
    assert record.reply_count == 0
    assert record.first_reply is None
    assert record.last_reply is None
    assert record.message_id is None
