"""Image upload storage for email-template bodies and signatures."""

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

MAX_TEMPLATE_IMAGE_SIZE = 5 * 1024 * 1024
TEMPLATE_IMAGE_CHUNK_SIZE = 1024 * 1024
ALLOWED_TEMPLATE_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})


class EmailTemplateImageError(RuntimeError):
    """Base error for email-template image upload failures."""


class EmailTemplateImageValidationError(EmailTemplateImageError):
    """Raised when an uploaded image fails validation."""


class EmailTemplateImageTooLargeError(EmailTemplateImageValidationError):
    """Raised when an uploaded image exceeds the configured size limit."""


@dataclass(frozen=True, slots=True)
class EmailTemplateImageUploadResult:
    """Stored image metadata returned to API clients."""

    file_name: str
    file_size: int


class EmailTemplateImageService:
    """Validate and store public images used by email templates."""

    def __init__(self, upload_folder: Path) -> None:
        self.upload_folder = upload_folder

    def initialize(self) -> None:
        """Ensure the public template image folder exists."""
        self.upload_folder.mkdir(parents=True, exist_ok=True)

    def upload_image(
        self,
        source: BinaryIO,
        original_file_name: str,
    ) -> EmailTemplateImageUploadResult:
        """Store one validated image using a collision-resistant safe filename."""
        safe_original_name = self._normalize_original_name(original_file_name)
        content, file_size = self._read_file_bytes(source)
        self._verify_image(content)

        self.upload_folder.mkdir(parents=True, exist_ok=True)
        while True:
            stored_file_name = f"{uuid4().hex}_{safe_original_name}"
            stored_path = (self.upload_folder / stored_file_name).resolve()
            if stored_path.parent != self.upload_folder.resolve():
                raise EmailTemplateImageValidationError("Invalid image filename.")
            try:
                with stored_path.open("xb") as destination:
                    destination.write(content)
                return EmailTemplateImageUploadResult(
                    file_name=stored_file_name,
                    file_size=file_size,
                )
            except FileExistsError:
                continue

    def _read_file_bytes(self, source: BinaryIO) -> tuple[bytes, int]:
        source.seek(0)
        buffer = BytesIO()
        file_size = 0
        while chunk := source.read(TEMPLATE_IMAGE_CHUNK_SIZE):
            file_size += len(chunk)
            if file_size > MAX_TEMPLATE_IMAGE_SIZE:
                raise EmailTemplateImageTooLargeError(
                    "Image exceeds the maximum upload size of 5 MB."
                )
            buffer.write(chunk)
        if file_size == 0:
            raise EmailTemplateImageValidationError("Uploaded image must not be empty.")
        return buffer.getvalue(), file_size

    @staticmethod
    def _verify_image(content: bytes) -> None:
        try:
            with Image.open(BytesIO(content)) as image:
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise EmailTemplateImageValidationError(
                "Uploaded file must be a valid image."
            ) from exc

    @staticmethod
    def _normalize_original_name(file_name: str) -> str:
        normalized = file_name.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not normalized or normalized in {".", ".."}:
            raise EmailTemplateImageValidationError("A valid image filename is required.")
        if len(normalized) > 255:
            raise EmailTemplateImageValidationError(
                "Image filename must not exceed 255 characters."
            )
        base = Path(normalized).stem.strip()
        suffix = Path(normalized).suffix.lower()
        if suffix not in ALLOWED_TEMPLATE_IMAGE_EXTENSIONS:
            raise EmailTemplateImageValidationError(
                "Only jpg, jpeg, png, gif, and webp images are allowed."
            )
        safe_base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._-")
        if not safe_base:
            safe_base = "image"
        return f"{safe_base[:200]}{suffix}"
