"""User-account (app/web) authentication for iSolarCloud.

Clean-room Python reimplementation of the reverse-engineered iSolarCloud **app/web
login**, which authenticates with a normal user email + password instead of the OpenAPI
developer OAuth flow (`Auth`). It is a third source of truth alongside developer-OAuth
and local Modbus (KRoperUK/sungrow-hass#267, this library #40).

Protocol and app-level constants were reimplemented from the **MIT-licensed**
``MortJC/homebridge-platform-isolarcloud``
(https://github.com/MortJC/homebridge-platform-isolarcloud, MIT (c) 2019 MortJC). No
GPL-licensed source (e.g. GoSungrow) was used.

.. warning::
    This is an **unofficial** API — not Sungrow's documented OpenAPI. It can change or
    break without notice and may be subject to Sungrow's terms of service. It is opt-in
    and should be treated as brittle. Credentials are only sent to iSolarCloud over TLS
    and are never logged.

Envelope (every call):

* A random 16-byte AES key is generated per request (``"web"`` + 13 random chars).
* The JSON body is AES-128-ECB (PKCS7) encrypted, hex-encoded and upper-cased.
* The AES key is RSA (PKCS#1 v1.5) encrypted into the ``x-random-secret-key`` header so
  the server can decrypt the body; ``x-limit-obj`` carries the RSA-encrypted user id.
* The response body is AES-decrypted with the same key and parsed as JSON, reusing the
  same ``{result_code/result_msg, result_data}`` envelope as the OpenAPI.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import string
import time
from typing import Any

from aiohttp import ClientSession, ClientTimeout
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from . import AuthError, PySolarCloudException, Server

_LOGGER = logging.getLogger(__name__)

# App-level constants from the MIT-licensed MortJC/homebridge-platform-isolarcloud.
# These identify the (web) client to iSolarCloud; they are not per-user secrets.
APP_KEY = "B0455FBE7AA0328DB57B59AA729F05D8"
ACCESS_KEY = "9grzgbmxdsp3arfmmgq347xjbza4ysps"
# ``sys_code`` for the web client (the phone app uses 900).
SYS_CODE = "200"

# RSA public login key (the one the reference client actually uses — an inline URL-safe
# base64 DER, converted here to standard PEM). The repo also ships a *different*
# ``loginkey.pem``; if login ever fails with this key, that alternate is the fallback to
# try. Overridable via ``UserAuth(public_key_pem=...)`` so a key rotation needs no release.
PUBLIC_KEY_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCkecphb6vgsBx4LJknKKes+eyj7+RKQ3fikF5B6\n"
    "7EObZ3t4moFZyMGuuJPiadYdaxvRqtxyblIlVM7omAasROtKRhtgKwwRxo2a6878qBhTgUVlsqugp\n"
    "I/7ZC9RmO2Rpmr8WzDeAapGANfHN5bVr7G7GYGwIrjvyxMrAVit/oM4wIDAQAB\n"
    "-----END PUBLIC KEY-----\n"
)

_LOGIN_PATH = "/v1/userService/login"
_PLANT_LIST_PATH = "/v1/powerStationService/getPsList"

# Documented result codes meaning the session/login is invalid → re-login (Appendix 2).
_LOGIN_INVALID_CODES = frozenset({"E00003", "1"})

DEFAULT_TIMEOUT = 30


def _random_aes_key() -> str:
    """Return a fresh 16-char AES-128 key (``"web"`` + 13 random alphanumerics)."""
    alphabet = string.ascii_letters + string.digits
    return "web" + "".join(secrets.choice(alphabet) for _ in range(13))


def _random_nonce(length: int = 32) -> str:
    """Return a random alphanumeric nonce."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _aes_encrypt(payload: dict[str, Any], key: str) -> str:
    """AES-128-ECB (PKCS7) encrypt a JSON payload → upper-cased hex string."""
    raw = json.dumps(payload).encode("utf-8")
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(raw) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key.encode("utf-8")), modes.ECB()).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return ciphertext.hex().upper()


def _aes_decrypt(data: str, key: str) -> dict[str, Any]:
    """Reverse :func:`_aes_encrypt` — decrypt an upper-cased hex string → JSON dict."""
    ciphertext = bytes.fromhex(data)
    decryptor = Cipher(algorithms.AES(key.encode("utf-8")), modes.ECB()).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    raw = unpadder.update(padded) + unpadder.finalize()
    return json.loads(raw.decode("utf-8"))


def _rsa_encrypt(value: str, public_key_pem: str) -> str:
    """RSA (PKCS#1 v1.5) encrypt a short string with the login public key → base64."""
    public_key = load_pem_public_key(public_key_pem.encode("utf-8"))
    ciphertext = public_key.encrypt(value.encode("utf-8"), asym_padding.PKCS1v15())  # type: ignore[union-attr]
    return base64.b64encode(ciphertext).decode("ascii")


class UserAuth:
    """Authenticate to iSolarCloud with a user account (email + password).

    Unlike :class:`Auth` (developer OAuth), this uses the app/web login. Call
    :meth:`async_get_plants` (or :meth:`async_request` for other endpoints); the token is
    fetched lazily on first use and re-fetched when the server reports it invalid.
    """

    def __init__(
        self,
        host: Server | str,
        email: str,
        password: str,
        *,
        websession: ClientSession | None = None,
        app_key: str = APP_KEY,
        access_key: str = ACCESS_KEY,
        public_key_pem: str = PUBLIC_KEY_PEM,
        lang: str = "_en_US",
    ) -> None:
        """Initialise the user-account auth.

        If ``websession`` is not supplied, an owned session with a request timeout is
        created and closed by :meth:`async_close` / ``async with`` exit.
        """
        self.host = host.value if isinstance(host, Server) else host
        self._email = email
        self._password = password
        self.app_key = app_key
        self.access_key = access_key
        self.public_key_pem = public_key_pem
        self.lang = lang
        self._owns_session = websession is None
        if websession is None:
            websession = ClientSession(raise_for_status=True, timeout=ClientTimeout(total=DEFAULT_TIMEOUT))
        self.websession = websession
        self.token: str | None = None
        self.user_id: str | None = None
        # Serialise logins so concurrent callers don't each spend a login.
        self._login_lock: asyncio.Lock | None = None

    async def async_close(self) -> None:
        """Close the underlying session, but only if it was created internally."""
        if self._owns_session and self.websession is not None and not self.websession.closed:
            await self.websession.close()

    async def __aenter__(self) -> UserAuth:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.async_close()

    def _common(self) -> dict[str, Any]:
        """Common request fields shared by every call."""
        return {
            "appkey": self.app_key,
            "api_key_param": {"timestamp": int(time.time() * 1000), "nonce": _random_nonce()},
        }

    async def _post(self, path: str, body: dict[str, Any], *, user_id: str = "") -> dict[str, Any]:
        """AES/RSA-envelope a request, POST it, and return the decrypted JSON body."""
        key = _random_aes_key()
        headers = {
            "content-type": "application/json;charset=UTF-8",
            "sys_code": SYS_CODE,
            "x-access-key": self.access_key,
            "x-random-secret-key": _rsa_encrypt(key, self.public_key_pem),
            "x-limit-obj": _rsa_encrypt(user_id, self.public_key_pem),
        }
        encrypted = _aes_encrypt(body, key)
        resp = await self.websession.request("post", f"{self.host}{path}", data=encrypted, headers=headers)
        resp.raise_for_status()
        text = await resp.text()
        return _aes_decrypt(text, key)

    @staticmethod
    def _succeeded(data: dict[str, Any]) -> bool:
        """True if the response envelope indicates success."""
        return data.get("result_msg") == "success" or str(data.get("result_code")) == "1"

    async def async_login(self) -> None:
        """Log in with the user credentials and store the token + user id.

        The login endpoint returns the **success envelope** (``result_code`` ``"1"``) even
        for a rejected account/password — the real signal is ``result_data.login_state ==
        "1"`` plus a token. ``login_state == "0"`` means the credentials (or region) were
        rejected; that is surfaced as a typed :class:`AuthError` carrying the API message
        and the remaining-attempts count (so callers can avoid triggering a lockout) rather
        than a misleading generic "success" error.
        """
        body = {**self._common(), "user_account": self._email, "user_password": self._password}
        data = await self._post(_LOGIN_PATH, body, user_id="")
        raw_result = data.get("result_data")
        result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
        token = result.get("token")
        login_state = str(result.get("login_state", ""))
        if not self._succeeded(data) or login_state == "0" or not token:
            msg = result.get("msg") or data.get("result_msg") or data.get("result_code")
            remain = result.get("remain_times")
            suffix = f" ({remain} attempt(s) remaining)" if remain not in (None, "") else ""
            _LOGGER.error("iSolarCloud user login failed: %s%s", msg, suffix)
            raise AuthError({"error": "user_login_failed", "error_description": f"Login failed: {msg}{suffix}"})
        self.token = str(token)
        self.user_id = str(result.get("user_id"))
        _LOGGER.debug("iSolarCloud user login successful")

    async def async_get_token(self) -> str:
        """Return a valid token, logging in (once, serialised) if needed."""
        if self.token is not None:
            return self.token
        if self._login_lock is None:
            self._login_lock = asyncio.Lock()
        async with self._login_lock:
            if self.token is None:
                await self.async_login()
        assert self.token is not None
        return self.token

    async def async_request(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make an authenticated request, re-logging in once if the token is rejected."""
        await self.async_get_token()
        payload = {
            **self._common(),
            "user_id": self.user_id,
            "token": self.token,
            "lang": self.lang,
            **(body or {}),
        }
        data = await self._post(path, payload, user_id=self.user_id or "")
        # The token can expire server-side; re-login once and retry before giving up.
        if not self._succeeded(data) and str(data.get("result_code")) in _LOGIN_INVALID_CODES:
            _LOGGER.debug("iSolarCloud token rejected (%s); re-logging in", data.get("result_code"))
            self.token = None
            await self.async_get_token()
            payload["user_id"] = self.user_id
            payload["token"] = self.token
            data = await self._post(path, payload, user_id=self.user_id or "")
        if not self._succeeded(data):
            raise PySolarCloudException.from_response(data)
        return data

    async def async_get_plants(self) -> list[dict[str, Any]]:
        """Return the plants on the account (minimal read to prove the client, #40)."""
        data = await self.async_request(_PLANT_LIST_PATH, {"valid_flag": "1,3"})
        result = data.get("result_data") or {}
        page_list = result.get("pageList")
        return list(page_list) if isinstance(page_list, list) else []
