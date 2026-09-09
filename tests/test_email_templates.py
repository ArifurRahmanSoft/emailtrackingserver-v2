"""Focused tests for the independent email-template management module."""

from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.email_template_routes as template_routes
from app.models.email_template import EmailTemplateBase
from app.services.email_template_images import (
    EmailTemplateImageService,
    MAX_TEMPLATE_IMAGE_SIZE,
)
from app.services.email_templates import EmailTemplateService
from main import app


def build_service() -> EmailTemplateService:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    EmailTemplateBase.metadata.create_all(engine)
    service = EmailTemplateService(None)
    service._engine = engine
    service._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return service


def test_email_templates_table_is_isolated() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    EmailTemplateBase.metadata.create_all(engine)
    assert {column["name"] for column in inspect(engine).get_columns("email_templates")} == {
        "id", "template_name", "subject", "body_html", "signature_html", "status",
        "created_by", "created_at", "updated_at",
    }


def test_template_crud_and_validation() -> None:
    service = build_service()
    template = service.create_template("Follow Up", "Subject", "<p>Hello</p>", "<p>Regards</p>")
    assert template.status == "active"
    assert service.list_templates()[0].id == template.id
    template = service.update_template(template.id, "Updated", "New subject", "<p>Body</p>", None, "inactive")
    assert template.template_name == "Updated"
    service.delete_template(template.id)
    assert service.list_templates() == []


def test_template_api_validation_and_crud() -> None:
    original = template_routes.template_service
    template_routes.template_service = build_service()
    try:
        with TestClient(app) as client:
            invalid = client.post("/api/email-templates", json={"subject": "x", "body_html": "y"})
            assert invalid.status_code == 400
            created = client.post("/api/email-templates", json={
                "template_name": "Follow Up", "subject": "Business", "body_html": "<p>Hello</p>",
                "signature_html": "<p>Regards</p>", "created_by": "user001",
            })
            assert created.status_code == 201
            template_id = created.json()["id"]
            assert client.get("/api/email-templates").status_code == 200
            assert client.get(f"/api/email-templates/{template_id}").status_code == 200
            updated = client.put(f"/api/email-templates/{template_id}", json={
                "template_name": "Updated", "subject": "New", "body_html": "<p>Body</p>",
            })
            assert updated.status_code == 200
            assert client.delete(f"/api/email-templates/{template_id}").status_code == 200
            assert client.get(f"/api/email-templates/{template_id}").status_code == 404
    finally:
        template_routes.template_service = original


def make_image_bytes(image_format: str) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (2, 2), color="white").save(buffer, format=image_format)
    return buffer.getvalue()


def configure_temp_image_uploads(monkeypatch, tmp_path: Path) -> EmailTemplateImageService:
    service = EmailTemplateImageService(tmp_path)
    service.initialize()
    monkeypatch.setattr(template_routes, "template_image_service", service)
    for route in app.routes:
        if getattr(route, "path", None) == "/uploads/email_templates":
            route.app.directory = str(tmp_path)
            route.app.all_directories = [str(tmp_path)]
            break
    return service


def test_template_image_upload_png_and_public_url(
    monkeypatch, tmp_path: Path
) -> None:
    configure_temp_image_uploads(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/email-templates/upload-image",
            files={"file": ("company_logo.png", make_image_bytes("PNG"), "image/png")},
        )

        assert response.status_code == 201
        payload = response.json()
        assert payload["success"] is True
        assert payload["file_name"].endswith("_company_logo.png")
        assert payload["url"].endswith(
            f"/uploads/email_templates/{payload['file_name']}"
        )
        public_response = client.get(f"/uploads/email_templates/{payload['file_name']}")
        assert public_response.status_code == 200
        assert public_response.headers["content-type"] == "image/png"


def test_template_image_upload_jpg_works(monkeypatch, tmp_path: Path) -> None:
    configure_temp_image_uploads(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/email-templates/upload-image",
            files={"file": ("banner.jpg", make_image_bytes("JPEG"), "image/jpeg")},
        )

    assert response.status_code == 201
    assert response.json()["file_name"].endswith("_banner.jpg")


def test_template_image_upload_rejects_invalid_extension(
    monkeypatch, tmp_path: Path
) -> None:
    configure_temp_image_uploads(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/email-templates/upload-image",
            files={"file": ("script.html", b"<script></script>", "text/html")},
        )

    assert response.status_code == 400
    assert "jpg, jpeg, png, gif, and webp" in response.json()["error"]["message"]


def test_template_image_upload_rejects_oversize_file(
    monkeypatch, tmp_path: Path
) -> None:
    configure_temp_image_uploads(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/email-templates/upload-image",
            files={
                "file": (
                    "large.png",
                    b"x" * (MAX_TEMPLATE_IMAGE_SIZE + 1),
                    "image/png",
                )
            },
        )

    assert response.status_code == 400
    assert "5 MB" in response.json()["error"]["message"]


def test_template_image_duplicate_filename_gets_unique_stored_names(
    monkeypatch, tmp_path: Path
) -> None:
    configure_temp_image_uploads(monkeypatch, tmp_path)
    image_bytes = make_image_bytes("PNG")

    with TestClient(app) as client:
        first = client.post(
            "/api/email-templates/upload-image",
            files={"file": ("logo.png", image_bytes, "image/png")},
        )
        second = client.post(
            "/api/email-templates/upload-image",
            files={"file": ("logo.png", image_bytes, "image/png")},
        )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["file_name"] != second.json()["file_name"]
    assert len(list(tmp_path.glob("*_logo.png"))) == 2
