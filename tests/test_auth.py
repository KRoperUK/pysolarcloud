"""Tests for typed token-refresh error handling (KRoperUK fork)."""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from pysolarcloud import Auth, PySolarCloudException, TokenRefreshError


def _auth() -> Auth:
    return Auth(
        host="https://gateway.isolarcloud.eu",
        appkey="k",
        access_key="s",
        app_id="1",
        websession=MagicMock(),
    )


@pytest.mark.asyncio
async def test_refresh_failure_raises_token_refresh_error():
    """A refresh response without an access token raises TokenRefreshError, not KeyError."""
    auth = _auth()
    auth.tokens = {
        "access_token": "old",
        "refresh_token": "r",
        "expires_at": 0,
    }  # expired
    auth.async_refresh_tokens = AsyncMock(return_value={"error": "invalid_grant"})

    with pytest.raises(TokenRefreshError) as exc:
        await auth.async_get_access_token()

    # It's a PySolarCloudException subclass with a typed error code and the raw response.
    assert isinstance(exc.value, PySolarCloudException)
    assert exc.value.error == "token_refresh_failed"
    assert exc.value.response == {"error": "invalid_grant"}


@pytest.mark.asyncio
async def test_refresh_success_rotates_tokens():
    """A successful refresh rotates the stored tokens and returns the new access token."""
    auth = _auth()
    auth.tokens = {"access_token": "old", "refresh_token": "r1", "expires_at": 0}
    auth.async_refresh_tokens = AsyncMock(
        return_value={"access_token": "new", "refresh_token": "r2", "expires_in": 3600}
    )

    token = await auth.async_get_access_token()

    assert token == "new"
    assert auth.tokens["refresh_token"] == "r2"


@pytest.mark.asyncio
async def test_valid_token_is_not_refreshed():
    """A still-valid token is returned without a refresh call."""
    auth = _auth()
    auth.tokens = {
        "access_token": "tok",
        "refresh_token": "r",
        "expires_at": int(time.time()) + 9999,
    }
    auth.async_refresh_tokens = AsyncMock()

    assert await auth.async_get_access_token() == "tok"
    auth.async_refresh_tokens.assert_not_called()


@pytest.mark.asyncio
async def test_no_tokens_raises_auth_not_initialised():
    """Requesting a token before authorizing raises a typed auth error."""
    auth = _auth()
    auth.tokens = None

    with pytest.raises(PySolarCloudException) as exc:
        await auth.async_get_access_token()
    assert exc.value.error == "auth_not_initialised"


@pytest.mark.asyncio
async def test_async_close_closes_internally_created_session():
    """An internally-created ClientSession is owned and closed by async_close()."""
    auth = Auth(host="https://gateway.isolarcloud.eu", appkey="k", access_key="s", app_id="1")
    assert auth.websession.closed is False
    await auth.async_close()
    assert auth.websession.closed is True


@pytest.mark.asyncio
async def test_async_close_leaves_injected_session_open():
    """An injected session is not owned, so async_close() must not close it."""
    session = MagicMock()
    session.closed = False
    session.close = AsyncMock()
    auth = Auth(
        host="https://gateway.isolarcloud.eu",
        appkey="k",
        access_key="s",
        app_id="1",
        websession=session,
    )
    await auth.async_close()
    session.close.assert_not_called()


@pytest.mark.asyncio
async def test_context_manager_closes_owned_session():
    """Using Auth as an async context manager closes an owned session on exit."""
    async with Auth(host="https://gateway.isolarcloud.eu", appkey="k", access_key="s", app_id="1") as auth:
        session = auth.websession
        assert session.closed is False
    assert session.closed is True
