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


def test_cors_origins_default_to_angular_localhost(monkeypatch):
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)

    settings = load_settings()

    assert settings.cors_allowed_origins == (
        "http://localhost:4200",
        "http://127.0.0.1:4200",
        "https://emailautomationreporting.netlify.app"
    )


def test_cors_origins_support_comma_separated_environment_values(monkeypatch):
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "[http://localhost:4200, https://angular-v2.example.com,https://emailautomationreporting.netlify.app]",
    )

    settings = load_settings()

    assert settings.cors_allowed_origins == (
        "http://localhost:4200",
        "https://angular-v2.example.com","https://emailautomationreporting.netlify.app"
    )


def test_cors_origins_do_not_allow_wildcard(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")

    settings = load_settings()

    assert "*" not in settings.cors_allowed_origins
    assert settings.cors_allowed_origins == (
        "http://localhost:4200",
        "http://127.0.0.1:4200","https://emailautomationreporting.netlify.app"
    )
