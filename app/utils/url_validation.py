"""Validation helpers for tracked redirect destinations."""

from urllib.parse import urlsplit


def is_valid_http_url(value: str) -> bool:
    """Return whether a complete, safe HTTP(S) destination was supplied."""
    if not value or any(ord(character) < 32 for character in value):
        return False

    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme.lower() in {"http", "https"}
            and bool(parsed.netloc)
            and parsed.hostname is not None
        )
    except ValueError:
        return False
