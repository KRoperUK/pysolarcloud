"""Tests for typed token-refresh error handling (KRoperUK fork)."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from pysolarcloud import Auth, PySolarCloudException, Server, TokenRefreshError


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
async def test_concurrent_expired_token_refreshes_exactly_once():
    """Concurrent callers sharing one Auth spend the single-use refresh token only once.

    The refresh token is rotated on first use, so a second concurrent refresh with the
    same token fails upstream. An asyncio.Lock + expiry re-check must let only the first
    waiter refresh; the rest return the freshly stored token.
    """
    auth = _auth()
    auth.tokens = {"access_token": "old", "refresh_token": "r1", "expires_at": 0}  # expired

    call_count = 0

    async def fake_refresh(refresh_token, **kwargs):
        nonlocal call_count
        call_count += 1
        # Yield control so an unsynchronized second caller would interleave into the
        # refresh block and double-spend the refresh token.
        await asyncio.sleep(0)
        return {"access_token": "new", "refresh_token": "r2", "expires_in": 3600}

    auth.async_refresh_tokens = AsyncMock(side_effect=fake_refresh)

    tokens = await asyncio.gather(
        auth.async_get_access_token(),
        auth.async_get_access_token(),
    )

    assert tokens == ["new", "new"]
    assert call_count == 1
    auth.async_refresh_tokens.assert_awaited_once()
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


def test_auth_url_builds_region_specific_url():
    """auth_url encodes the redirect and picks the region's web host + cloudId (Europe)."""
    auth = _auth()  # Europe host
    url = auth.auth_url("https://example.com/callback")
    assert url.startswith("https://web3.isolarcloud.eu/#/authorized-app")
    assert "cloudId=3" in url
    assert "applicationId=1" in url
    assert "redirectUrl=https%3A%2F%2Fexample.com%2Fcallback" in url


def test_auth_url_china_and_australia_regions():
    """Each server maps to its own auth host + cloudId."""
    cn = Auth(host=Server.China, appkey="k", access_key="s", app_id="9", websession=MagicMock())
    assert "auth" in cn.auth_url("https://cb") or "web3" in cn.auth_url("https://cb")
    assert "cloudId=1" in cn.auth_url("https://cb")
    au = Auth(host=Server.Australia, appkey="k", access_key="s", app_id="9", websession=MagicMock())
    assert "auweb3.isolarcloud.com" in au.auth_url("https://cb")
    assert "cloudId=7" in au.auth_url("https://cb")
    intl = Auth(host=Server.International, appkey="k", access_key="s", app_id="9", websession=MagicMock())
    assert "web3.isolarcloud.com.hk" in intl.auth_url("https://cb")
    assert "cloudId=2" in intl.auth_url("https://cb")


@pytest.mark.asyncio
async def test_request_builds_authenticated_post_body():
    """request() posts to host+path with appkey/lang in the body and auth headers."""
    session = MagicMock()
    session.request = AsyncMock(return_value=MagicMock())
    auth = Auth(host="https://gateway.isolarcloud.eu", appkey="appk", access_key="sec", app_id="1", websession=session)
    auth.tokens = {"access_token": "tok", "refresh_token": "r", "expires_at": int(time.time()) + 9999}

    await auth.request("openapi/platform/foo", {"ps_id": "123"}, lang="_de_DE")

    args, kwargs = session.request.call_args
    assert args[0] == "post"
    assert args[1] == "https://gateway.isolarcloud.eu/openapi/platform/foo"
    assert kwargs["json"] == {"ps_id": "123", "appkey": "appk", "lang": "_de_DE"}
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    assert kwargs["headers"]["x-access-key"] == "sec"


@pytest.mark.asyncio
async def test_async_fetch_tokens_posts_authorization_code():
    """async_fetch_tokens posts the code to the apiManage/token endpoint and returns json."""
    resp = MagicMock()
    resp.json = AsyncMock(return_value={"access_token": "a", "refresh_token": "b", "expires_in": 3600})
    session = MagicMock()
    session.request = AsyncMock(return_value=resp)
    auth = Auth(host="https://gateway.isolarcloud.eu", appkey="appk", access_key="sec", app_id="1", websession=session)

    out = await auth.async_fetch_tokens("the-code", "https://cb")

    args, kwargs = session.request.call_args
    assert args[1].endswith("/openapi/apiManage/token")
    assert kwargs["json"]["code"] == "the-code"
    assert kwargs["json"]["grant_type"] == "authorization_code"
    assert kwargs["json"]["redirect_uri"] == "https://cb"
    assert out["refresh_token"] == "b"


@pytest.mark.asyncio
async def test_async_refresh_tokens_posts_to_refresh_endpoint():
    """async_refresh_tokens posts the refresh token to the apiManage/refreshToken endpoint."""
    resp = MagicMock()
    resp.json = AsyncMock(return_value={"access_token": "a", "refresh_token": "b", "expires_in": 3600})
    session = MagicMock()
    session.request = AsyncMock(return_value=resp)
    auth = Auth(host="https://gateway.isolarcloud.eu", appkey="appk", access_key="sec", app_id="1", websession=session)

    out = await auth.async_refresh_tokens("old-refresh")

    args, kwargs = session.request.call_args
    assert args[1].endswith("/openapi/apiManage/refreshToken")
    assert kwargs["json"] == {"appkey": "appk", "refresh_token": "old-refresh"}
    assert kwargs["headers"]["x-access-key"] == "sec"
    assert out["access_token"] == "a"


@pytest.mark.asyncio
async def test_async_authorize_stores_tokens_on_success():
    """A successful token fetch is stored as the rotating tokens dict with an expiry."""
    auth = _auth()
    auth.async_fetch_tokens = AsyncMock(return_value={"access_token": "a", "refresh_token": "b", "expires_in": 3600})

    await auth.async_authorize("code", "https://cb")

    assert auth.tokens["access_token"] == "a"
    assert auth.tokens["refresh_token"] == "b"
    assert auth.tokens["expires_at"] > int(time.time())


@pytest.mark.asyncio
async def test_async_authorize_noop_when_no_access_token():
    """A fetch response without an access token leaves tokens unset."""
    auth = _auth()
    auth.tokens = None
    auth.async_fetch_tokens = AsyncMock(return_value={"error": "bad_code"})

    await auth.async_authorize("code", "https://cb")

    assert auth.tokens is None
