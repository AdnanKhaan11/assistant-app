from __future__ import annotations

from pathlib import Path

import pytest

from backend.memory.auth_store import AuthStore
from backend.services.authentication import (
    AuthenticationFailed,
    AuthenticationRateLimited,
    AuthenticationService,
    EmailAlreadyRegistered,
)


@pytest.fixture
def authentication(tmp_path: Path) -> AuthenticationService:
    """Provide an isolated auth service backed by a temporary SQLite database."""
    return AuthenticationService(AuthStore(tmp_path / "assistant.db"))


@pytest.mark.asyncio
async def test_register_login_authenticate_and_logout(authentication: AuthenticationService) -> None:
    """Check a registered account can authenticate through a revocable opaque session."""
    user = await authentication.register("person@example.test", "a strong password")
    session = await authentication.login("PERSON@example.test", "a strong password")

    assert user.user_id == session.user.user_id
    assert session.token
    assert session.token != user.password_hash
    assert await authentication.authenticate(session.token) == user
    assert await authentication.logout(session.token)
    assert not await authentication.logout(session.token)
    assert await authentication.authenticate(session.token) is None


@pytest.mark.asyncio
async def test_duplicate_registration_is_reported_without_replacing_account(
    authentication: AuthenticationService,
) -> None:
    """Check duplicate email registration fails while the original credentials remain valid."""
    await authentication.register("person@example.test", "first password")

    with pytest.raises(EmailAlreadyRegistered):
        await authentication.register("PERSON@example.test", "second password")

    assert (await authentication.login("person@example.test", "first password")).user.email == (
        "person@example.test"
    )


@pytest.mark.asyncio
async def test_login_failure_is_generic_for_unknown_email_and_wrong_password(
    authentication: AuthenticationService,
) -> None:
    """Check unknown accounts and invalid passwords produce the same domain failure."""
    await authentication.register("person@example.test", "correct password")

    with pytest.raises(AuthenticationFailed):
        await authentication.login("missing@example.test", "wrong")
    with pytest.raises(AuthenticationFailed):
        await authentication.login("person@example.test", "wrong")


@pytest.mark.asyncio
async def test_login_attempt_limit_returns_rate_limit_error(authentication: AuthenticationService) -> None:
    """Check the eleventh recent attempt for one email is rejected before authentication."""
    for _ in range(10):
        with pytest.raises(AuthenticationFailed):
            await authentication.login("missing@example.test", "wrong")

    with pytest.raises(AuthenticationRateLimited):
        await authentication.login("missing@example.test", "wrong")


@pytest.mark.asyncio
async def test_successful_login_resets_attempt_limit(authentication: AuthenticationService) -> None:
    """Check a successful login clears failed-attempt history for that account."""
    await authentication.register("person@example.test", "correct password")
    for _ in range(9):
        with pytest.raises(AuthenticationFailed):
            await authentication.login("person@example.test", "wrong")

    assert await authentication.login("person@example.test", "correct password")
    assert await authentication.login("person@example.test", "correct password")
