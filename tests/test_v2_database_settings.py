import pytest

from config.settings import load_settings


def test_v2_database_url_accepts_dedicated_database(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:password@example.com/email_tracking_v2?sslmode=require",
    )

    settings = load_settings()

    assert settings.expected_database_name == "email_tracking_v2"
    assert settings.database_url is not None


def test_v2_database_url_rejects_other_postgresql_database(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:password@example.com/email_tracking?sslmode=require",
    )

    with pytest.raises(ValueError, match="email_tracking_v2"):
        load_settings()


def test_v2_database_name_can_be_overridden_for_isolated_tests(monkeypatch):
    monkeypatch.setenv("EXPECTED_DATABASE_NAME", "email_tracking_v2_test")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:password@example.com/email_tracking_v2_test",
    )

    settings = load_settings()

    assert settings.expected_database_name == "email_tracking_v2_test"


def test_v2_deployment_identity_comes_from_environment(monkeypatch):
    monkeypatch.setenv("APP_NAME", "EmailTrackingServer-V2")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example-v2.invalid")

    settings = load_settings()

    assert settings.application_name == "EmailTrackingServer-V2"
    assert settings.environment == "production"
    assert settings.public_base_url == "https://example-v2.invalid"
