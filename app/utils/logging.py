"""Application logging with one dated file per day."""

import logging
from datetime import date
from pathlib import Path
from threading import RLock


class ApplicationContextFilter(logging.Filter):
    """Add deployment identity to every log record."""

    def __init__(self, application_name: str, environment: str) -> None:
        super().__init__()
        self.application_name = application_name
        self.environment = environment

    def filter(self, record: logging.LogRecord) -> bool:
        record.application_name = self.application_name
        record.environment = self.environment
        return True


class DailyFileHandler(logging.Handler):
    """Write records to ``YYYY-MM-DD.log`` and switch files at midnight."""

    def __init__(self, log_directory: Path, encoding: str = "utf-8") -> None:
        super().__init__()
        self.log_directory = log_directory
        self.encoding = encoding
        self._current_date: date | None = None
        self._stream = None
        self._stream_lock = RLock()

    def _ensure_stream(self) -> None:
        today = date.today()
        if self._stream is not None and self._current_date == today:
            return

        if self._stream is not None:
            self._stream.close()

        self.log_directory.mkdir(parents=True, exist_ok=True)
        self._stream = (self.log_directory / f"{today.isoformat()}.log").open(
            "a", encoding=self.encoding
        )
        self._current_date = today

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            with self._stream_lock:
                self._ensure_stream()
                assert self._stream is not None
                self._stream.write(message + "\n")
                self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        with self._stream_lock:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
        super().close()


def configure_logging(
    log_directory: Path,
    log_level: str,
    application_name: str,
    environment: str,
) -> None:
    """Configure console and dated-file logging for the application."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    context_filter = ApplicationContextFilter(application_name, environment)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(application_name)s | "
        "%(environment)s | %(name)s | %(message)s"
    )

    file_handler = DailyFileHandler(log_directory)
    file_handler.addFilter(context_filter)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.addFilter(context_filter)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
