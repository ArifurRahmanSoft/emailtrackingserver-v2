"""Database service for email-template management."""

from datetime import datetime, timezone
import logging
from uuid import UUID

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.email_template import EmailTemplate, EmailTemplateBase

logger = logging.getLogger(__name__)


class EmailTemplateServiceError(RuntimeError):
    """Base error for template storage failures."""


class EmailTemplateDatabaseUnavailableError(EmailTemplateServiceError):
    """Raised when template storage is unavailable."""


class EmailTemplateNotFoundError(EmailTemplateServiceError):
    """Raised when a template UUID does not exist."""


class EmailTemplateValidationError(EmailTemplateServiceError):
    """Raised when required template values are empty."""


class EmailTemplateService:
    """CRUD operations for the isolated email_templates table."""

    def __init__(self, database_url: str | None) -> None:
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._configuration_error: str | None = None
        if database_url:
            try:
                self._engine = create_engine(
                    self._normalize_database_url(database_url),
                    pool_pre_ping=True, pool_recycle=300,
                    connect_args={"connect_timeout": 10},
                )
                self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)
            except Exception as exc:
                self._configuration_error = str(exc)

    def initialize(self) -> None:
        """Ensure the table exists for local/test databases; production uses Alembic."""
        engine = self._require_engine()
        EmailTemplateBase.metadata.create_all(engine)

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()

    def create_template(self, template_name: str, subject: str, body_html: str,
                        signature_html: str | None = None, status: str = "active",
                        created_by: str | None = None,
                        created_at: datetime | None = None) -> EmailTemplate:
        name, clean_subject, body = self._required(template_name, "template_name"), self._required(subject, "subject"), self._required(body_html, "body_html")
        timestamp = self._utc(created_at or datetime.now(timezone.utc))
        try:
            with self._require_session_factory()() as session:
                template = EmailTemplate(template_name=name, subject=clean_subject, body_html=body,
                    signature_html=self._optional(signature_html), status=self._optional(status) or "active",
                    created_by=self._optional(created_by), created_at=timestamp, updated_at=timestamp)
                session.add(template)
                session.commit()
                return template
        except EmailTemplateValidationError:
            raise
        except Exception as exc:
            raise EmailTemplateDatabaseUnavailableError(f"Unable to create email template: {exc}") from exc

    def list_templates(self) -> list[EmailTemplate]:
        try:
            with self._require_session_factory()() as session:
                return list(session.scalars(select(EmailTemplate).order_by(EmailTemplate.created_at.desc())))
        except Exception as exc:
            raise EmailTemplateDatabaseUnavailableError(f"Unable to list email templates: {exc}") from exc

    def get_template(self, template_id: UUID) -> EmailTemplate:
        try:
            with self._require_session_factory()() as session:
                template = session.get(EmailTemplate, template_id)
                if template is None:
                    raise EmailTemplateNotFoundError("Email template not found.")
                return template
        except EmailTemplateNotFoundError:
            raise
        except Exception as exc:
            raise EmailTemplateDatabaseUnavailableError(f"Unable to get email template: {exc}") from exc

    def update_template(self, template_id: UUID, template_name: str, subject: str, body_html: str,
                        signature_html: str | None, status: str) -> EmailTemplate:
        name, clean_subject, body = self._required(template_name, "template_name"), self._required(subject, "subject"), self._required(body_html, "body_html")
        try:
            with self._require_session_factory()() as session:
                template = session.get(EmailTemplate, template_id)
                if template is None:
                    raise EmailTemplateNotFoundError("Email template not found.")
                template.template_name, template.subject, template.body_html = name, clean_subject, body
                template.signature_html, template.status = self._optional(signature_html), self._optional(status) or "active"
                template.updated_at = datetime.now(timezone.utc)
                session.commit()
                return template
        except (EmailTemplateNotFoundError, EmailTemplateValidationError):
            raise
        except Exception as exc:
            raise EmailTemplateDatabaseUnavailableError(f"Unable to update email template: {exc}") from exc

    def delete_template(self, template_id: UUID) -> None:
        try:
            with self._require_session_factory()() as session:
                template = session.get(EmailTemplate, template_id)
                if template is None:
                    raise EmailTemplateNotFoundError("Email template not found.")
                session.delete(template)
                session.commit()
        except EmailTemplateNotFoundError:
            raise
        except Exception as exc:
            raise EmailTemplateDatabaseUnavailableError(f"Unable to delete email template: {exc}") from exc

    @staticmethod
    def _required(value: str | None, field: str) -> str:
        cleaned = value.strip() if value is not None else ""
        if not cleaned:
            raise EmailTemplateValidationError(f"{field} is required.")
        return cleaned

    @staticmethod
    def _optional(value: str | None) -> str | None:
        return None if value is None or not value.strip() else value.strip()

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @staticmethod
    def _normalize_database_url(url: str) -> str:
        return url.replace("postgres://", "postgresql+psycopg://", 1).replace("postgresql://", "postgresql+psycopg://", 1)

    def _require_engine(self) -> Engine:
        if self._engine is None:
            raise EmailTemplateDatabaseUnavailableError(self._configuration_error or "DATABASE_URL is not configured.")
        return self._engine

    def _require_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            raise EmailTemplateDatabaseUnavailableError(self._configuration_error or "DATABASE_URL is not configured.")
        return self._session_factory
