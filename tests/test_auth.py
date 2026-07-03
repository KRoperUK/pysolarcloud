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
