"""Request and response models for simple authentication APIs."""

from datetime import datetime

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
