"""Transparent tracking-pixel generation."""

from functools import lru_cache
from io import BytesIO

from PIL import Image


@lru_cache(maxsize=1)
def get_transparent_pixel() -> bytes:
    """Create and cache a valid transparent 1×1 PNG image."""
    output = BytesIO()
    image = Image.new("RGBA", (1, 1), color=(0, 0, 0, 0))
    image.save(output, format="PNG")
    return output.getvalue()
