"""Database operations for simple Angular authentication."""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from uuid import UUID

from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.auth import AuthBase, SystemUser

logger = logging.getLogger(__name__)


class AuthServiceError(RuntimeError):
    """Base error for authentication infrastructure failures."""


class AuthDatabaseUnavailableError(AuthServiceError):
    """Raised when authentication storage is unavailable."""


class DuplicateSystemUserError(AuthServiceError):
    """Raised when a user_id is already registered."""


class InvalidCredentialsError(AuthServiceError):
    """Raised when login credentials do not match a stored user."""


class SystemUserNotFoundError(AuthServiceError):
    """Raised when a requested system user UUID does not exist."""


@dataclass(frozen=True, slots=True)
class AuthUserResult:
    """Authentication user data returned by register/login operations."""

    user_id: str
    role: str
    register_date: datetime


@dataclass(frozen=True, slots=True)
class AuthUserListResult:
    """Non-sensitive user data returned by the list API."""

    id: UUID
    user_id: str
    role: str
    register_date: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AuthUserUpdateResult:
    """Non-sensitive user data returned after an update."""

    id: UUID
    user_id: str
    role: str
    register_date: datetime
    updated_at: datetime
    user_id_before_update: str
    role_before_update: str
    created_at_before_update: datetime


class AuthService:
    """Create and authenticate system users without affecting tracking APIs."""

    def __init__(self, database_url: str | None) -> None:
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._configuration_error: str | None = None

        if database_url:
            try:
                self._engine = create_engine(
                    self._normalize_database_url(database_url),
                    pool_pre_ping=True,
                    pool_recycle=300,
                    connect_args={"connect_timeout": 10},
                )
                self._session_factory = sessionmaker(
                    bind=self._engine,
                    expire_on_commit=False,
                )
            except Exception as exc:
                self._configuration_error = str(exc)

    def initialize(self) -> None:
        """Create the authentication table when it is absent."""
        engine = self._require_engine()
        AuthBase.metadata.create_all(engine)

    def dispose(self) -> None:
        """Dispose of the authentication connection pool."""
        if self._engine is not None:
            self._engine.dispose()

    def register_user(
        self,
        user_id: str,
        password: str,
        role: str,
        registered_at: datetime | None = None,
    ) -> AuthUserResult:
        """Register one system user, rejecting duplicate user_id values."""
        session_factory = self._require_session_factory()
        clean_user_id = user_id.strip()
        clean_role = role.strip()
        clean_password = password.strip()
        timestamp = self._as_utc(registered_at or datetime.now(timezone.utc))

        try:
            with session_factory() as session:
                existing = session.scalar(
                    select(SystemUser.id).where(SystemUser.user_id == clean_user_id)
                )
                if existing is not None:
                    raise DuplicateSystemUserError(
                        f"user_id '{clean_user_id}' already exists."
                    )

                user = SystemUser(
                    user_id=clean_user_id,
                    password=clean_password,
                    role=clean_role,
                    register_date=timestamp,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                session.add(user)
                session.commit()
                return AuthUserResult(
                    user_id=user.user_id,
                    role=user.role,
                    register_date=user.register_date,
                )
        except DuplicateSystemUserError:
            raise
        except Exception as exc:
            raise AuthDatabaseUnavailableError(
                f"Unable to register system user: {exc}"
            ) from exc

    def login_user(self, user_id: str, password: str) -> AuthUserResult:
        """Authenticate one system user by user_id and password."""
        session_factory = self._require_session_factory()
        clean_user_id = user_id.strip()
        clean_password = password.strip()

        try:
            with session_factory() as session:
                user = session.scalar(
                    select(SystemUser).where(SystemUser.user_id == clean_user_id)
                )
                if user is None or user.password != clean_password:
                    raise InvalidCredentialsError("Invalid user_id or password.")

                return AuthUserResult(
                    user_id=user.user_id,
                    role=user.role,
                    register_date=user.register_date,
                )
        except InvalidCredentialsError:
            raise
        except Exception as exc:
            raise AuthDatabaseUnavailableError(
                f"Unable to authenticate system user: {exc}"
            ) from exc

    def list_users(self) -> list[AuthUserListResult]:
        """Return all system users newest-first without password data."""
        session_factory = self._require_session_factory()

        try:
            with session_factory() as session:
                users = session.scalars(
                    select(SystemUser).order_by(SystemUser.register_date.desc())
                ).all()
                return [
                    AuthUserListResult(
                        id=user.id,
                        user_id=user.user_id,
                        role=user.role,
                        register_date=user.register_date,
                        created_at=user.created_at,
                        updated_at=user.updated_at,
                    )
                    for user in users
                ]
        except Exception as exc:
            raise AuthDatabaseUnavailableError(
                f"Unable to list system users: {exc}"
            ) from exc

    def get_client_codes(self) -> list[str]:
        """Return non-empty user_id values for campaign client-code dropdowns."""
        session_factory = self._require_session_factory()

        try:
            with session_factory() as session:
                return list(
                    session.scalars(
                        select(SystemUser.user_id)
                        .where(
                            SystemUser.user_id.is_not(None),
                            func.trim(SystemUser.user_id) != "",
                        )
                        .order_by(SystemUser.user_id.asc())
                    )
                )
        except Exception as exc:
            raise AuthDatabaseUnavailableError(
                f"Unable to list client codes: {exc}"
            ) from exc

    def update_user(
        self,
        user_uuid: UUID,
        user_id: str,
        password: str,
        role: str,
        updated_at: datetime | None = None,
    ) -> AuthUserUpdateResult:
        """Update one system user while preserving register_date and created_at."""
        session_factory = self._require_session_factory()
        clean_user_id = user_id.strip()
        clean_password = password.strip()
        clean_role = role.strip()
        timestamp = self._as_utc(updated_at or datetime.now(timezone.utc))

        try:
            with session_factory() as session:
                user = session.get(SystemUser, user_uuid)
                if user is None:
                    raise SystemUserNotFoundError(f"user id '{user_uuid}' not found.")

                duplicate = session.scalar(
                    select(SystemUser.id).where(
                        SystemUser.user_id == clean_user_id,
                        SystemUser.id != user_uuid,
                    )
                )
                if duplicate is not None:
                    raise DuplicateSystemUserError(
                        f"user_id '{clean_user_id}' already exists."
                    )

                user_id_before_update = user.user_id
                role_before_update = user.role
                created_at_before_update = user.created_at

                user.user_id = clean_user_id
                user.password = clean_password
                user.role = clean_role
                user.updated_at = timestamp
                session.commit()

                return AuthUserUpdateResult(
                    id=user.id,
                    user_id=user.user_id,
                    role=user.role,
                    register_date=user.register_date,
                    updated_at=user.updated_at,
                    user_id_before_update=user_id_before_update,
                    role_before_update=role_before_update,
                    created_at_before_update=created_at_before_update,
                )
        except (DuplicateSystemUserError, SystemUserNotFoundError):
            raise
        except Exception as exc:
            raise AuthDatabaseUnavailableError(
                f"Unable to update system user: {exc}"
            ) from exc

    @staticmethod
    def _normalize_database_url(database_url: str) -> str:
        """Use psycopg 3 for normal PostgreSQL URLs."""
        if database_url.startswith("postgres://"):
            return database_url.replace("postgres://", "postgresql+psycopg://", 1)
        if database_url.startswith("postgresql://"):
            return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return database_url

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """Return a timezone-aware UTC datetime."""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _require_engine(self) -> Engine:
        """Return the configured engine or fail with a clear auth error."""
        if self._engine is None:
            raise AuthDatabaseUnavailableError(
                self._configuration_error or "DATABASE_URL is not configured."
            )
        return self._engine

    def _require_session_factory(self) -> sessionmaker[Session]:
        """Return the configured session factory or fail with a clear auth error."""
        if self._session_factory is None:
            raise AuthDatabaseUnavailableError(
                self._configuration_error or "DATABASE_URL is not configured."
            )
        return self._session_factory
