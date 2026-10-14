"""Define auth HTTP routes; requests provide credentials and responses expose safe account fields."""
from __future__ import annotations

import hmac
import json
import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path as APIPath, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from backend.memory.auth_store import AdminAuditEvent, ManagedAuthUser, StoredAuthUser
from backend.services.authentication import (
    AuthenticationFailed,
    AuthenticationRateLimited,
    AuthenticationService,
    AdminActionRejected,
    AdminUserNotFound,
    EmailAlreadyRegistered,
)

SESSION_COOKIE = "assistant_session"
CSRF_COOKIE = "assistant_csrf"
SESSION_TTL_SECONDS = 8 * 60 * 60
router = APIRouter(prefix="/api/auth", tags=["authentication"])


class RegistrationRequest(BaseModel):
    """Validate the email and password submitted to create a regular user account."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    """Validate the email and password submitted to start a user session."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=1, max_length=128)


class PasswordChangeRequest(BaseModel):
    """Validate the current password and replacement password for account security."""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class AdminUserUpdateRequest(BaseModel):
    """Validate the role and active status an administrator assigns to an account."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "admin"]
    is_active: bool


class AuthUserResponse(BaseModel):
    """Expose safe account identity and role fields without credentials or session data."""

    user_id: str
    email: str
    role: str
    created_at: str

    @classmethod
    def from_user(cls, user: StoredAuthUser) -> AuthUserResponse:
        """Convert internal `user` data into the credential-free public response."""
        return cls(
            user_id=user.user_id,
            email=user.email,
            role=user.role,
            created_at=user.created_at,
        )


class LoginResponse(BaseModel):
    """Return the signed-in user while the opaque token remains in an HttpOnly cookie."""

    user: AuthUserResponse


class AdminUserResponse(BaseModel):
    """Expose account-management fields while excluding password and session data."""

    user_id: str
    email: str
    role: Literal["user", "admin"]
    is_active: bool
    created_at: str

    @classmethod
    def from_user(cls, user: ManagedAuthUser) -> AdminUserResponse:
        """Convert safe `user` storage data to an administrator-facing response."""
        return cls(
            user_id=user.user_id,
            email=user.email,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at,
        )


class AdminAuditEventResponse(BaseModel):
    """Expose safe account-audit details without user IDs, credentials, or session data."""

    event_id: int
    action: str
    actor_email: str | None
    target_email: str | None
    details: dict[str, str | bool]
    created_at: str

    @classmethod
    def from_event(cls, event: AdminAuditEvent) -> AdminAuditEventResponse:
        """Convert a stored audit event into the safe administrator response shape."""
        try:
            details = json.loads(event.details_json)
        except json.JSONDecodeError:
            details = {}
        if not isinstance(details, dict) or not all(
            isinstance(key, str) and isinstance(value, (str, bool))
            for key, value in details.items()
        ):
            details = {}
        return cls(
            event_id=event.event_id,
            action=event.action,
            actor_email=event.actor_email,
            target_email=event.target_email,
            details=details,
            created_at=event.created_at,
        )


async def current_user(request: Request) -> StoredAuthUser:
    """Authenticate the `assistant_session` cookie and return its active account."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    user = await _auth_service(request).authenticate(token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    return user


async def require_admin(
    user: Annotated[StoredAuthUser, Depends(current_user)],
) -> StoredAuthUser:
    """Allow account-management routes only when authenticated `user` has the admin role."""
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required.")
    return user


@router.post("/register", response_model=AuthUserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegistrationRequest, request: Request) -> AuthUserResponse:
    """Create a regular account or return a safe conflict/rate-limit response."""
    try:
        user = await _auth_service(request).register(body.email, body.password)
    except EmailAlreadyRegistered:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account already uses that email.",
        ) from None
    except AuthenticationRateLimited:
        raise _rate_limited() from None
    return AuthUserResponse.from_user(user)


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request, response: Response) -> LoginResponse:
    """Verify credentials and set secure session and CSRF cookies on success."""
    try:
        session = await _auth_service(request).login(body.email, body.password)
    except AuthenticationFailed:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email or password is incorrect.",
        ) from None
    except AuthenticationRateLimited:
        raise _rate_limited() from None
    _set_auth_cookies(response, request, session.token)
    return LoginResponse(user=AuthUserResponse.from_user(session.user))


@router.get("/me", response_model=AuthUserResponse)
async def get_current_user(user: Annotated[StoredAuthUser, Depends(current_user)]) -> AuthUserResponse:
    """Return the authenticated account's public profile fields."""
    return AuthUserResponse.from_user(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> Response:
    """Revoke the session after CSRF validation and clear both browser cookies."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    validate_csrf(request)
    await _auth_service(request).logout(token)
    _clear_auth_cookies(response, request)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    response: Response,
    user: Annotated[StoredAuthUser, Depends(current_user)],
) -> Response:
    """Change the authenticated user's password and require login again afterward."""
    validate_csrf(request)
    try:
        await _auth_service(request).change_password(user.user_id, body.current_password, body.new_password)
    except AuthenticationFailed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect or the session is no longer valid.",
        ) from None
    _clear_auth_cookies(response, request)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/admin/users", response_model=list[AdminUserResponse])
async def list_admin_users(
    user: Annotated[StoredAuthUser, Depends(require_admin)],
    request: Request,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AdminUserResponse]:
    """Return a bounded page of users to an administrator identified by `user`."""
    records = await _auth_service(request).list_users(limit, offset)
    return [AdminUserResponse.from_user(record) for record in records]


@router.get("/admin/audit", response_model=list[AdminAuditEventResponse])
async def list_admin_audit_events(
    request: Request,
    user: Annotated[StoredAuthUser, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AdminAuditEventResponse]:
    """Return a bounded page of safe account-audit events to an administrator."""
    events = await _auth_service(request).list_admin_audit_events(limit, offset)
    return [AdminAuditEventResponse.from_event(event) for event in events]


@router.patch("/admin/users/{target_user_id}", response_model=AdminUserResponse)
async def update_admin_user(
    target_user_id: Annotated[str, APIPath(pattern=r"^[a-f0-9]{32}$")],
    body: AdminUserUpdateRequest,
    request: Request,
    user: Annotated[StoredAuthUser, Depends(require_admin)],
) -> AdminUserResponse:
    """Update `target_user_id` from `body` for admin `user` after CSRF validation."""
    validate_csrf(request)
    try:
        record = await _auth_service(request).update_user(
            user.user_id,
            target_user_id,
            body.role,
            body.is_active,
        )
    except AdminUserNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.") from None
    except AdminActionRejected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The account change would remove the last active administrator or lock out this account.",
        ) from None
    return AdminUserResponse.from_user(record)


def _auth_service(request: Request) -> AuthenticationService:
    """Read the startup-created authentication service from this app's state."""
    return request.app.state.authentication


def _set_auth_cookies(response: Response, request: Request, token: str) -> None:
    """Set `token` and a readable CSRF nonce with matching secure cookie attributes."""
    secure = request.url.scheme == "https"
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        secrets.token_urlsafe(32),
        max_age=SESSION_TTL_SECONDS,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )


def _clear_auth_cookies(response: Response, request: Request) -> None:
    """Expire both auth cookies using the same path and security attributes as login."""
    secure = request.url.scheme == "https"
    for cookie_name, httponly in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
        response.delete_cookie(cookie_name, path="/", secure=secure, httponly=httponly, samesite="strict")


def validate_csrf(request: Request) -> None:
    """Require a same-origin CSRF cookie/header pair before changing session state."""
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    header_token = request.headers.get("x-csrf-token", "")
    if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed.")


def _rate_limited() -> HTTPException:
    """Build the shared 429 response for throttled auth operations."""
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many attempts. Try again later.",
        headers={"Retry-After": "900"},
    )
