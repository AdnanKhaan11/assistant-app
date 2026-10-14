"""Expose authenticated, per-account preferences through a small JSON API."""
from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from backend.auth_api import current_user, validate_csrf
from backend.memory.auth_store import StoredAuthUser
from backend.memory.user_settings_store import UserSettingsRecord, UserSettingsStore

router = APIRouter(prefix="/api/settings", tags=["user settings"])


class UserSettingsRequest(BaseModel):
    """Restrict user-editable preferences to the supported light/dark theme field."""

    model_config = ConfigDict(extra="forbid")

    theme: Literal["light", "dark"]


class UserSettingsResponse(BaseModel):
    """Return only the caller's supported preferences and their update timestamp."""

    theme: Literal["light", "dark"]
    updated_at: str | None

    @classmethod
    def from_record(cls, record: UserSettingsRecord) -> UserSettingsResponse:
        """Convert the storage result to its public response shape."""
        return cls(theme=record.theme, updated_at=record.updated_at)


@router.get("", response_model=UserSettingsResponse)
async def get_user_settings(
    request: Request,
    user: Annotated[StoredAuthUser, Depends(current_user)],
) -> UserSettingsResponse:
    """Read preferences only for the authenticated account."""
    store = UserSettingsStore(request.app.state.settings.db_path)
    record = await asyncio.to_thread(store.get, user.user_id)
    return UserSettingsResponse.from_record(record)


@router.put("", response_model=UserSettingsResponse)
async def update_user_settings(
    body: UserSettingsRequest,
    request: Request,
    user: Annotated[StoredAuthUser, Depends(current_user)],
) -> UserSettingsResponse:
    """Validate CSRF and save the authenticated account's selected theme."""
    validate_csrf(request)
    store = UserSettingsStore(request.app.state.settings.db_path)
    record = await asyncio.to_thread(store.set_theme, user.user_id, body.theme)
    return UserSettingsResponse.from_record(record)
