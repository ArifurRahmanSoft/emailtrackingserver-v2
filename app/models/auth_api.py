"""Request and response models for simple authentication APIs."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AuthRegisterRequest(BaseModel):
    """Payload used to register one system user."""

    user_id: str = Field(..., min_length=1, max_length=20)
    password: str = Field(..., min_length=1, max_length=20)
    role: str = Field(..., min_length=1, max_length=15)


class AuthLoginRequest(BaseModel):
    """Payload used to authenticate one system user."""

    user_id: str = Field(..., min_length=1, max_length=20)
    password: str = Field(..., min_length=1, max_length=20)


class AuthUserResponse(BaseModel):
    """Successful register/login response without password data."""

    success: bool
    user_id: str
    role: str
    register_date: datetime


class AuthUserListItem(BaseModel):
    """One system user returned by the user list API."""

    id: UUID
    user_id: str
    role: str
    register_date: datetime
    created_at: datetime
    updated_at: datetime


class AuthUpdateUserRequest(BaseModel):
    """Payload used to update editable system user fields."""

    user_id: str = Field(..., min_length=1, max_length=20)
    password: str = Field(..., min_length=1, max_length=20)
    role: str = Field(..., min_length=1, max_length=15)


class AuthUpdateUserResponse(BaseModel):
    """Successful system user update response without password data."""

    success: bool
    id: UUID
    user_id: str
    role: str
    register_date: datetime
    updated_at: datetime


class ClientCodeListResponse(BaseModel):
    """Read-only list of client codes sourced from system users."""

    success: bool
    client_codes: list[str]
