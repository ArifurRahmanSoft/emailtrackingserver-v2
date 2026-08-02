"""Tests for Version 2 simple authentication infrastructure."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

import app.api.auth_routes as auth_route_module
from app.models.auth import AuthBase, SystemUser
from app.services.auth import (
    AuthService,
    AuthUserListResult,
    AuthUserResult,
    AuthUserUpdateResult,
    DuplicateSystemUserError,
    InvalidCredentialsError,
    SystemUserNotFoundError,
)
from main import app


def build_auth_service() -> tuple[AuthService, sessionmaker]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AuthBase.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = AuthService(None)
    service._engine = engine
    service._session_factory = session_factory
    return service, session_factory


def test_system_users_table_has_required_indexes() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AuthBase.metadata.create_all(engine)

    indexes = {index["name"] for index in inspect(engine).get_indexes("system_users")}
    columns = {column["name"] for column in inspect(engine).get_columns("system_users")}
    engine.dispose()

    assert {
        "id",
        "user_id",
        "password",
        "role",
        "register_date",
        "created_at",
        "updated_at",
    }.issubset(columns)
    assert "ix_system_users_user_id" in indexes
    assert "ix_system_users_role" in indexes


def test_user_registration_succeeds_without_returning_password() -> None:
    service, session_factory = build_auth_service()
    register_time = datetime(2026, 8, 2, 9, 30, tzinfo=timezone.utc)

    result = service.register_user(
        user_id="admin",
        password="123456",
        role="ADMIN",
        registered_at=register_time,
    )

    with session_factory() as session:
        user = session.scalar(select(SystemUser).where(SystemUser.user_id == "admin"))

    assert result.user_id == "admin"
    assert result.role == "ADMIN"
    assert result.register_date.replace(tzinfo=None) == register_time.replace(
        tzinfo=None
    )
    assert not hasattr(result, "password")
    assert user is not None
    assert user.password == "123456"


def test_duplicate_user_id_raises_conflict_error() -> None:
    service, _ = build_auth_service()
    service.register_user("admin", "123456", "ADMIN")

    with pytest.raises(DuplicateSystemUserError):
        service.register_user("admin", "abcdef", "USER")


def test_login_succeeds_with_correct_credentials() -> None:
    service, _ = build_auth_service()
    register_time = datetime(2026, 8, 2, 9, 30, tzinfo=timezone.utc)
    service.register_user("admin", "123456", "ADMIN", register_time)

    result = service.login_user("admin", "123456")

    assert result.user_id == "admin"
    assert result.role == "ADMIN"
    assert result.register_date.replace(tzinfo=None) == register_time.replace(
        tzinfo=None
    )
    assert not hasattr(result, "password")


def test_login_invalid_user_id_raises_invalid_credentials() -> None:
    service, _ = build_auth_service()

    with pytest.raises(InvalidCredentialsError):
        service.login_user("missing", "123456")


def test_login_invalid_password_raises_invalid_credentials() -> None:
    service, _ = build_auth_service()
    service.register_user("admin", "123456", "ADMIN")

    with pytest.raises(InvalidCredentialsError):
        service.login_user("admin", "wrong")


def test_list_users_returns_all_users_newest_first_without_password() -> None:
    service, _ = build_auth_service()
    old_time = datetime(2026, 8, 1, 9, 30, tzinfo=timezone.utc)
    new_time = datetime(2026, 8, 2, 9, 30, tzinfo=timezone.utc)
    service.register_user("old_user", "oldpass", "USER", old_time)
    service.register_user("new_user", "newpass", "ADMIN", new_time)

    users = service.list_users()

    assert [user.user_id for user in users] == ["new_user", "old_user"]
    assert all(not hasattr(user, "password") for user in users)


def test_update_user_succeeds_and_preserves_original_dates() -> None:
    service, session_factory = build_auth_service()
    register_time = datetime(2026, 8, 1, 9, 30, tzinfo=timezone.utc)
    update_time = datetime(2026, 8, 2, 10, 45, tzinfo=timezone.utc)
    service.register_user("admin", "123456", "ADMIN", register_time)

    with session_factory() as session:
        user = session.scalar(select(SystemUser).where(SystemUser.user_id == "admin"))
        assert user is not None
        user_uuid = user.id
        original_register_date = user.register_date
        original_created_at = user.created_at

    result = service.update_user(
        user_uuid=user_uuid,
        user_id="manager",
        password="abcdef",
        role="MANAGER",
        updated_at=update_time,
    )

    with session_factory() as session:
        updated_user = session.get(SystemUser, user_uuid)

    assert result.id == user_uuid
    assert result.user_id == "manager"
    assert result.role == "MANAGER"
    assert result.register_date == original_register_date
    assert result.updated_at.replace(tzinfo=None) == update_time.replace(tzinfo=None)
    assert not hasattr(result, "password")
    assert updated_user is not None
    assert updated_user.password == "abcdef"
    assert updated_user.register_date == original_register_date
    assert updated_user.created_at == original_created_at
    assert updated_user.updated_at.replace(tzinfo=None) == update_time.replace(
        tzinfo=None
    )


def test_update_user_duplicate_user_id_raises_conflict_error() -> None:
    service, session_factory = build_auth_service()
    service.register_user("admin", "123456", "ADMIN")
    service.register_user("manager", "abcdef", "MANAGER")

    with session_factory() as session:
        manager = session.scalar(
            select(SystemUser).where(SystemUser.user_id == "manager")
        )
        assert manager is not None

    with pytest.raises(DuplicateSystemUserError):
        service.update_user(manager.id, "admin", "newpass", "MANAGER")


def test_update_user_unknown_uuid_raises_not_found() -> None:
    service, _ = build_auth_service()

    with pytest.raises(SystemUserNotFoundError):
        service.update_user(uuid4(), "admin", "123456", "ADMIN")


def test_register_endpoint_returns_success_without_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_time = datetime(2026, 8, 2, 9, 30, tzinfo=timezone.utc)

    class FakeAuthService:
        def register_user(
            self,
            user_id: str,
            password: str,
            role: str,
            registered_at: datetime | None = None,
        ) -> AuthUserResult:
            assert user_id == "admin"
            assert password == "123456"
            assert role == "ADMIN"
            assert registered_at is not None
            return AuthUserResult(
                user_id=user_id,
                role=role,
                register_date=register_time,
            )

    monkeypatch.setattr(auth_route_module, "auth_service", FakeAuthService())
    client = TestClient(app)

    response = client.post(
        "/api/auth/register",
        json={"user_id": "admin", "password": "123456", "role": "ADMIN"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["success"] is True
    assert payload["user_id"] == "admin"
    assert payload["role"] == "ADMIN"
    assert "password" not in payload


def test_register_endpoint_duplicate_user_id_returns_http_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAuthService:
        def register_user(
            self,
            user_id: str,
            password: str,
            role: str,
            registered_at: datetime | None = None,
        ) -> AuthUserResult:
            raise DuplicateSystemUserError("duplicate")

    monkeypatch.setattr(auth_route_module, "auth_service", FakeAuthService())
    client = TestClient(app)

    response = client.post(
        "/api/auth/register",
        json={"user_id": "admin", "password": "123456", "role": "ADMIN"},
    )

    assert response.status_code == 409


def test_get_users_endpoint_returns_password_free_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest_time = datetime(2026, 8, 2, 9, 30, tzinfo=timezone.utc)
    older_time = datetime(2026, 8, 1, 9, 30, tzinfo=timezone.utc)
    newest_id = uuid4()
    older_id = uuid4()

    class FakeAuthService:
        def list_users(self) -> list[AuthUserListResult]:
            return [
                AuthUserListResult(
                    id=newest_id,
                    user_id="new_user",
                    role="ADMIN",
                    register_date=newest_time,
                    created_at=newest_time,
                    updated_at=newest_time,
                ),
                AuthUserListResult(
                    id=older_id,
                    user_id="old_user",
                    role="USER",
                    register_date=older_time,
                    created_at=older_time,
                    updated_at=older_time,
                ),
            ]

    monkeypatch.setattr(auth_route_module, "auth_service", FakeAuthService())
    client = TestClient(app)

    response = client.get("/api/auth/users")

    assert response.status_code == 200
    payload = response.json()
    assert [user["user_id"] for user in payload] == ["new_user", "old_user"]
    assert all("password" not in user for user in payload)
    assert payload[0]["id"] == str(newest_id)


def test_update_user_endpoint_returns_success_without_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_uuid = uuid4()
    register_time = datetime(2026, 8, 1, 9, 30, tzinfo=timezone.utc)
    update_time = datetime(2026, 8, 2, 10, 45, tzinfo=timezone.utc)

    class FakeAuthService:
        def update_user(
            self,
            user_uuid_arg,
            user_id: str,
            password: str,
            role: str,
            updated_at: datetime | None = None,
        ) -> AuthUserUpdateResult:
            assert user_uuid_arg == user_uuid
            assert user_id == "manager"
            assert password == "abcdef"
            assert role == "MANAGER"
            assert updated_at is not None
            return AuthUserUpdateResult(
                id=user_uuid,
                user_id=user_id,
                role=role,
                register_date=register_time,
                updated_at=update_time,
                user_id_before_update="admin",
                role_before_update="ADMIN",
                created_at_before_update=register_time,
            )

    monkeypatch.setattr(auth_route_module, "auth_service", FakeAuthService())
    client = TestClient(app)

    response = client.put(
        f"/api/auth/users/{user_uuid}",
        json={"user_id": "manager", "password": "abcdef", "role": "MANAGER"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["id"] == str(user_uuid)
    assert payload["user_id"] == "manager"
    assert payload["role"] == "MANAGER"
    assert "password" not in payload


def test_update_user_endpoint_duplicate_user_id_returns_http_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAuthService:
        def update_user(
            self,
            user_uuid,
            user_id: str,
            password: str,
            role: str,
            updated_at: datetime | None = None,
        ) -> AuthUserUpdateResult:
            raise DuplicateSystemUserError("duplicate")

    monkeypatch.setattr(auth_route_module, "auth_service", FakeAuthService())
    client = TestClient(app)

    response = client.put(
        f"/api/auth/users/{uuid4()}",
        json={"user_id": "admin", "password": "123456", "role": "ADMIN"},
    )

    assert response.status_code == 409


def test_update_user_endpoint_unknown_uuid_returns_http_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAuthService:
        def update_user(
            self,
            user_uuid,
            user_id: str,
            password: str,
            role: str,
            updated_at: datetime | None = None,
        ) -> AuthUserUpdateResult:
            raise SystemUserNotFoundError("missing")

    monkeypatch.setattr(auth_route_module, "auth_service", FakeAuthService())
    client = TestClient(app)

    response = client.put(
        f"/api/auth/users/{uuid4()}",
        json={"user_id": "admin", "password": "123456", "role": "ADMIN"},
    )

    assert response.status_code == 404


def test_login_endpoint_returns_success_without_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_time = datetime(2026, 8, 2, 9, 30, tzinfo=timezone.utc)

    class FakeAuthService:
        def login_user(self, user_id: str, password: str) -> AuthUserResult:
            assert user_id == "admin"
            assert password == "123456"
            return AuthUserResult(
                user_id=user_id,
                role="ADMIN",
                register_date=register_time,
            )

    monkeypatch.setattr(auth_route_module, "auth_service", FakeAuthService())
    client = TestClient(app)

    response = client.post(
        "/api/auth/login",
        json={"user_id": "admin", "password": "123456"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["user_id"] == "admin"
    assert payload["role"] == "ADMIN"
    assert "password" not in payload


@pytest.mark.parametrize(
    "payload",
    [
        {"user_id": "missing", "password": "123456"},
        {"user_id": "admin", "password": "wrong"},
    ],
)
def test_login_endpoint_invalid_credentials_return_http_401(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, str],
) -> None:
    class FakeAuthService:
        def login_user(self, user_id: str, password: str) -> AuthUserResult:
            raise InvalidCredentialsError("invalid")

    monkeypatch.setattr(auth_route_module, "auth_service", FakeAuthService())
    client = TestClient(app)

    response = client.post("/api/auth/login", json=payload)

    assert response.status_code == 401
