"""Strict timestamp parsing helpers for API query parameters."""

from datetime import datetime, timezone


def parse_iso8601_utc(value: str) -> datetime:
    """Parse an ISO-8601 timestamp and normalize it to UTC.

    Timestamps without an explicit offset are interpreted as UTC so desktop
    clients can persist and reuse server cursors consistently.
    """
    normalized = value.strip()
    if not normalized:
        raise ValueError("The timestamp is empty.")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("Invalid ISO-8601 datetime.") from exc

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
