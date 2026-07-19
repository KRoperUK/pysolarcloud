"""A Python library to interact with Sungrow's iSolarCloud API."""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from enum import StrEnum
from urllib.parse import quote_plus

from aiohttp import ClientResponse, ClientSession, ClientTimeout

_LOGGER = logging.getLogger(__name__)


class Server(StrEnum):
    """Enum of iSolarCloud servers."""

    China = "https://gateway.isolarcloud.com"
    International = "https://gateway.isolarcloud.com.hk"
    Europe = "https://gateway.isolarcloud.eu"
    Australia = "https://augateway.isolarcloud.com"

    @property
    def web_console_url(self) -> str:
        """Return the ``https://`` URL of the region's iSolarCloud web console.

        This is the user-facing dashboard (as opposed to the ``gateway.*`` API host
        the enum's value carries) — useful for the ``configuration_url`` field on HA
        device registry entries and for building "Visit iSolarCloud" links.
        """
        match self:
            case Server.China:
                return "https://web3.isolarcloud.com"
            case Server.International:
                return "https://web3.isolarcloud.com.hk"
            case Server.Europe:
                return "https://web3.isolarcloud.eu"
            case Server.Australia:
                return "https://auweb3.isolarcloud.com"
        # StrEnum with the four cases above is exhaustive at runtime, but keep the
        # explicit fallback so a future member can't return None by accident.
        raise ValueError(f"No web console URL configured for {self!r}")


class AbstractAuth(ABC):
    """Abstract class to make authenticated requests.

    Subclasses must implement the async_get_access_token method
    and may call async_fetch_tokens and async_refresh_tokens.
    """

    def __init__(
        self,
        websession: ClientSession,
        server: Server | str,
        client_id: str,
        client_secret: str,
        app_id: str,
    ):
        """Initialize the authorization session."""
        self.websession = websession
        self.host = server.value if isinstance(server, Server) else server
        self.appkey = client_id
        self.access_key = client_secret
        self.app_id = app_id

    def auth_url(self, redirect_uri: str) -> str:
        """Return the URL to authorize the user."""
        match self.host:
            case Server.China.value:
                auth_server = "web3.isolarcloud.com"
                cloud_id = 1
            case Server.International.value:
                auth_server = "web3.isolarcloud.com.hk"
                cloud_id = 2
            case Server.Europe.value:
                auth_server = "web3.isolarcloud.eu"
                cloud_id = 3
            case Server.Australia.value:
                auth_server = "auweb3.isolarcloud.com"
                cloud_id = 7
            case _:
                raise ValueError(f"Unknown iSolarCloud server host: {self.host}")
        return f"https://{auth_server}/#/authorized-app?cloudId={cloud_id}&applicationId={self.app_id}&redirectUrl={quote_plus(redirect_uri)}"

    @abstractmethod
    async def async_get_access_token(self) -> str:
        """Return a valid access token."""

    async def request(self, path: str, data: dict, *, lang: str = "_en_US", **kwargs) -> ClientResponse:
        """Make a request to iSolarCloud.

        Parameters:
        path -- the path to request
        data -- the data to send
        lang -- the language to use (default "_en_US", supported languages are "_en_US", "_zh_CN", "_ja_JP", "_es_ES", "_de_DE", "_pt_BR", "_fr_FR", "_it_IT", "_ko_KR", "_nl_NL", "_pl_PL", "_vi_VN", "_zh_TW"
        **kwargs -- additional arguments to pass to the request
        """
        if not path.startswith("/"):
            path = f"/{path}"
        if headers := kwargs.pop("headers", {}):
            headers = dict(headers)
        access_token = await self.async_get_access_token()
        headers = {
            **headers,
            "x-access-key": self.access_key,
            "Authorization": f"Bearer {access_token}",
        }
        body = {**data, "appkey": self.appkey, "lang": lang}
        return await self.websession.request(
            "post",
            f"{self.host}{path}",
            json=body,
            **kwargs,
            headers=headers,
        )

    async def async_fetch_tokens(self, code: str, redirect_uri: str, **kwargs) -> dict:
        """Fetch the access and refresh tokens."""
        if headers := kwargs.pop("headers", {}):
            headers = dict(headers)
        headers = {
            **headers,
            "x-access-key": self.access_key,
            "Content-type": "application/json",
        }
        body = {
            "appkey": self.appkey,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        response = await self.websession.request(
            "post",
            f"{self.host}/openapi/apiManage/token",
            json=body,
            headers=headers,
            **kwargs,
        )
        return await response.json()

    async def async_refresh_tokens(self, refresh_token: str, **kwargs) -> dict:
        """Refresh the access token."""
        if headers := kwargs.pop("headers", {}):
            headers = dict(headers)
        headers = {**headers, "x-access-key": self.access_key}
        body = {"appkey": self.appkey, "refresh_token": refresh_token}
        response = await self.websession.request(
            "post",
            f"{self.host}/openapi/apiManage/refreshToken",
            json=body,
            **kwargs,
            headers=headers,
        )
        return await response.json()


class Auth(AbstractAuth):
    """Class to authenticate with the SolarCloud API."""

    #: Default total timeout (seconds) applied to an internally-created session.
    DEFAULT_TIMEOUT = 30

    def __init__(
        self,
        host: str,
        appkey: str,
        access_key: str,
        app_id: str,
        *,
        websession: ClientSession | None = None,
    ):
        """Initialize the auth.

        If ``websession`` is not supplied, an owned ``ClientSession`` is created with a
        request timeout and closed by :meth:`async_close` (or on ``async with`` exit). An
        injected session is left untouched — the caller owns its lifecycle.
        """
        self._owns_session = websession is None
        if websession is None:
            websession = ClientSession(
                raise_for_status=True,
                timeout=ClientTimeout(total=self.DEFAULT_TIMEOUT),
            )
        super().__init__(websession, host, appkey, access_key, app_id)
        self.tokens = None
        # Serializes token refresh so concurrent callers can't both spend the
        # single-use refresh token. Created lazily inside the running loop to
        # avoid binding the lock to the wrong event loop.
        self._refresh_lock: asyncio.Lock | None = None

    async def async_close(self) -> None:
        """Close the underlying session, but only if it was created internally."""
        if self._owns_session and self.websession is not None and not self.websession.closed:
            await self.websession.close()

    async def __aenter__(self) -> "Auth":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.async_close()

    async def async_authorize(self, code, redirect_uri):
        """Authorize the user.

        Raises :class:`AuthError` if the token exchange fails.
        """
        ts = await self.async_fetch_tokens(code, redirect_uri)
        if "access_token" not in ts:
            _LOGGER.error("Authorization failed: %s", str(ts))
            raise AuthError({"error": "authorization_failed", "error_description": str(ts)})
        self.tokens = {
            "access_token": ts["access_token"],
            "refresh_token": ts["refresh_token"],
            "expires_at": int(time.time()) + ts["expires_in"] - 20,
        }
        _LOGGER.debug("Authorization succesful")

    async def async_get_access_token(self) -> str:
        """Return a valid access token."""
        if self.tokens is None:
            raise PySolarCloudException(
                {
                    "error": "auth_not_initialised",
                    "error_description": "You must authorize first.",
                }
            )
        # Fast path: a still-valid token needs no refresh and no lock.
        if self.tokens["expires_at"] >= int(time.time()):
            return self.tokens["access_token"]
        # Slow path: serialize refreshes. The refresh token is single-use — iSolarCloud
        # rotates it on first use — so concurrent callers must not both spend it. The
        # first waiter refreshes; the rest see the freshly stored token on the
        # authoritative expiry re-check inside the lock and skip the refresh.
        if self._refresh_lock is None:
            self._refresh_lock = asyncio.Lock()
        async with self._refresh_lock:
            if self.tokens["expires_at"] < int(time.time()):
                current_refresh_token = self.tokens["refresh_token"]
                ts = await self.async_refresh_tokens(current_refresh_token)
                if "access_token" not in ts:
                    # The refresh token is no longer valid; the caller must re-authorize.
                    # Raise a typed error rather than letting `ts["access_token"]` surface
                    # a bare KeyError that callers would have to string-match.
                    _LOGGER.error("Token refresh failed: %s", str(ts))
                    raise TokenRefreshError(ts)
                # iSolarCloud usually rotates the refresh token, but a partial-refresh
                # response (access_token only, no new refresh_token) does occur in the
                # wild. In that case the previous refresh token is still valid — keep
                # it instead of storing ``None`` and immediately breaking the next
                # refresh (see #62).
                self.tokens = {
                    "access_token": ts["access_token"],
                    "refresh_token": ts.get("refresh_token") or current_refresh_token,
                    "expires_at": int(time.time()) + ts["expires_in"] - 20,
                }
            return self.tokens["access_token"]


# Documented ``result_code`` values that mean the credentials / authorization are dead and
# the caller must re-authenticate (Appendix 2: API Error Code Definitions / Appendix 9).
# These map to :class:`AuthError`.
_AUTH_ERROR_CODES = frozenset({"E00003", "E900", "E919", "E912", "E914"})
# Documented quota / throttle ``result_code`` values (Appendix 2 / Appendix 9). These are
# transient — the caller should back off and retry rather than re-authenticate — so they map
# to :class:`RateLimitError`, NOT :class:`AuthError`.
_RATE_LIMIT_CODES = frozenset({"E998", "E999"})


class PySolarCloudException(Exception):
    """Exception class raised by PySolarCloud when communication with the iSolarCloud service fails.

    It can be constructed either from a raw error string, from a legacy ``{"error": ...}``
    envelope, or from a real iSolarCloud business response of the shape
    ``{"result_code", "result_msg", "result_data", "req_serial_num"}``. In every case the
    machine-readable code is exposed on ``.error`` (for the result_code shape this is the
    ``result_code`` string, e.g. ``"E00003"``), which downstream consumers match against their
    own ``AUTH_ERRORS`` sets.

    Prefer :meth:`from_response` at raise time: it returns the most specific subclass
    (:class:`AuthError` / :class:`RateLimitError`) for documented result codes so consumers can
    branch reauth-vs-retry by ``isinstance`` instead of maintaining a code list.
    """

    def __init__(self, err: dict | str):
        if isinstance(err, dict):
            # Prefer the legacy "error" key, fall back to the real API's "result_code".
            code = err.get("error") or err.get("result_code")
            self.error = code
            self.result_msg = err.get("result_msg")
            self.error_description = err.get("error_description") or self.result_msg
            self.req_serial_num = err.get("req_serial_num", None)
            super().__init__(self.error_description or code or str(err))
        else:
            super().__init__(err)
            self.error = err
            self.result_msg = None
            self.error_description = None
            self.req_serial_num = None

    @classmethod
    def from_response(cls, err: dict | str) -> "PySolarCloudException":
        """Return the most specific exception subclass for an iSolarCloud error response.

        Documented ``result_code`` values are mapped to typed subclasses (see
        ``_AUTH_ERROR_CODES`` / ``_RATE_LIMIT_CODES``); everything else falls back to the base
        ``PySolarCloudException``. This is the single chokepoint the business methods raise
        through, so ``isinstance(exc, AuthError)`` / ``isinstance(exc, RateLimitError)`` reliably
        classify the failure while ``.error`` still carries the raw code for backward compat.

        A factory is used rather than mutating ``self.__class__`` in ``__init__`` because an
        instance cannot change its own class after construction.
        """
        # Match the code the same way __init__ derives ``.error`` so classification and the
        # exposed attribute never disagree.
        code = (err.get("error") or err.get("result_code")) if isinstance(err, dict) else err
        if code in _AUTH_ERROR_CODES:
            return AuthError(err)
        if code in _RATE_LIMIT_CODES:
            return RateLimitError(err)
        return cls(err)


def _parse_retry_after(raw: object) -> float | None:
    """Best-effort coerce a rate-limit ``retry_after`` value to seconds, or ``None``.

    iSolarCloud has been observed to return the hint as an int, a float, or a numeric
    string, and (rarely) as ``None``/absent. Anything unparseable is dropped so a
    flaky server value can't leak through and mislead consumers into a bogus back-off.
    """
    if raw is None:
        return None
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


class AuthError(PySolarCloudException):
    """Raised when the iSolarCloud API rejects a request because the credentials are dead.

    A thin typed marker for downstream consumers that would rather catch a type than match
    ``.error`` against a set of result codes. ``.error`` still carries the raw code.
    """


class RateLimitError(PySolarCloudException):
    """Raised when the iSolarCloud API rejects a request because the call rate limit was hit.

    ``.retry_after`` (seconds) is populated when the API includes a ``retry_after`` (or a
    common alternate spelling) in the error envelope, so consumers can back off precisely
    instead of guessing at a doubling interval. ``None`` when the API doesn't advertise one.
    """

    #: Server-suggested back-off in seconds, or ``None`` when not advertised.
    retry_after: float | None

    def __init__(self, err: dict | str):
        super().__init__(err)
        if isinstance(err, dict):
            # Different iSolarCloud deployments (and the developer portal docs) spell
            # the hint differently; accept the observed variants rather than depending
            # on a single field name that a future firmware might rename.
            for key in ("retry_after", "retryAfter", "retry_in", "retry_seconds"):
                self.retry_after = _parse_retry_after(err.get(key))
                if self.retry_after is not None:
                    return
        self.retry_after = None


class TokenRefreshError(PySolarCloudException):
    """Raised when refreshing the access token fails (the response has no access token).

    This means the stored refresh token is no longer valid and the user must
    re-authorize. Catch this type (or its ``PySolarCloudException`` base) instead of
    inspecting a bare ``KeyError``. The raw refresh response is available as
    ``response`` for debugging.
    """

    def __init__(self, response: dict | None = None):
        super().__init__(
            {
                "error": "token_refresh_failed",
                "error_description": "Token refresh returned no access token",
            }
        )
        self.response = response


class DeviceNotWritableError(PySolarCloudException):
    """Raised when a control write is rejected because the device won't accept it.

    Distinguishes "the target device (EV charger, meter, permission-gated inverter)
    does not accept parameter writes" — a permanent, per-device condition — from
    generic API/task failures. Consumers can silently skip the device instead of
    treating the rejection as an unexpected error and retrying.

    The ``code`` returned by the device task envelope (e.g. ``"9"`` for
    "unsupported") is exposed via :attr:`device_code`, and the original raw response
    is available on :attr:`response` for debugging.
    """

    def __init__(self, response: dict, device_code: str | None = None):
        super().__init__(
            {
                "error": "device_not_writable",
                "error_description": (
                    f"Device rejected the parameter write (device code {device_code!r})"
                    if device_code
                    else "Device rejected the parameter write"
                ),
            }
        )
        self.response = response
        self.device_code = device_code


# Imported at the end so user_auth can import Server/exceptions from this module without
# a circular-import failure (the names above are already defined by the time this runs).
from .user_auth import UserAuth as UserAuth  # noqa: E402
from .user_control import UserControl as UserControl  # noqa: E402
