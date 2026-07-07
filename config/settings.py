"""Environment-driven application settings for EmailTrackingServer V2."""

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
EXPECTED_DATABASE_NAME = "email_tracking_v2"
DEFAULT_APPLICATION_NAME = "EmailTrackingServer-V2"
DEFAULT_ENVIRONMENT = "production"


def _load_env_file() -> None:
    """Load project-local environment variables without overriding Render."""
    if not ENV_FILE.is_file():
        return

    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _read_port() -> int:
    """Read and validate the HTTP port from the environment."""
    raw_port = os.getenv("PORT", "8000")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("PORT must be an integer.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535.")
    return port


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable runtime settings loaded from environment variables."""

    application_name: str
    environment: str
    public_base_url: str | None
    port: int
    log_level: str
    data_folder: Path
    log_folder: Path
    database_url: str | None
    expected_database_name: str

    @property
    def tracking_file(self) -> Path:
        """Return the configured Excel tracking workbook path."""
        return self.data_folder / "EmailTracking.xlsx"


def load_settings() -> Settings:
    """Load settings, using project-local defaults for local development."""
    _load_env_file()

    data_folder = Path(os.getenv("DATA_FOLDER", "data")).expanduser()
    if not data_folder.is_absolute():
        data_folder = PROJECT_ROOT / data_folder

    expected_database_name = (
        os.getenv("EXPECTED_DATABASE_NAME", EXPECTED_DATABASE_NAME).strip()
        or EXPECTED_DATABASE_NAME
    )
    database_url = _read_database_url(expected_database_name)

    return Settings(
        application_name=_read_non_empty(
            "APP_NAME", DEFAULT_APPLICATION_NAME
        ),
        environment=_read_non_empty("APP_ENV", DEFAULT_ENVIRONMENT),
        public_base_url=_read_optional("PUBLIC_BASE_URL"),
        port=_read_port(),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        data_folder=data_folder.resolve(),
        log_folder=PROJECT_ROOT / "logs",
        database_url=database_url,
        expected_database_name=expected_database_name,
    )


def _read_non_empty(key: str, default: str) -> str:
    """Read a required-style string setting with a safe local default."""
    return os.getenv(key, default).strip() or default


def _read_optional(key: str) -> str | None:
    """Read an optional string setting from the environment."""
    value = os.getenv(key)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _read_database_url(expected_database_name: str) -> str | None:
    """Read and validate the V2 PostgreSQL connection string."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return None

    _validate_v2_database_name(database_url, expected_database_name)
    return database_url


def _validate_v2_database_name(
    database_url: str,
    expected_database_name: str,
) -> None:
    """Prevent Version 2 from connecting to any non-V2 PostgreSQL database."""
    try:
        parsed_url = make_url(database_url)
    except ArgumentError as exc:
        raise ValueError("DATABASE_URL is not a valid database connection URL.") from exc

    if not parsed_url.drivername.startswith(("postgresql", "postgres")):
        return

    if parsed_url.database != expected_database_name:
        raise ValueError(
            "DATABASE_URL must point to the dedicated Version 2 database "
            f"'{expected_database_name}'."
        )
