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
# Daily-detail path (``getPsDetail(ps_id, date_id)`` in the app's HttpRequest.java).
_PLANT_DETAIL_PATH = "/v1/powerStationService/getPsDetail"
# Realtime household view path (``getPsDetailBalcony``/``getPsDetailOversea`` in the app).
_PLANT_DETAIL_WITH_TYPE_PATH = "/v1/powerStationService/getPsDetailWithPsType"
_DEVICE_LIST_PATH = "/v1/devService/queryDeviceList"
# Per-device realtime, keyed by ``ps_key`` (the app's canonical path; #89). The old
# ``/v1/devService/queryDevice`` this replaced does not exist in the app.
_DEVICE_REALTIME_PATH = "/v1/devService/queryDeviceRealTimeDataByPsKeys"
_HISTORICAL_DATA_PATH = "/v1/commonService/queryMutiPointDataList"

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
    # ``bytes.hex()`` is typed ``str`` in Python's stubs but some ``cryptography``
    # versions in CI expose ``ciphertext`` as an ``Any`` (protocol-typed) — coerce
    # to ``str`` explicitly so the return type is stable across environments.
    return str(ciphertext.hex().upper())


def _aes_decrypt(data: str, key: str) -> dict[str, Any]:
    """Reverse :func:`_aes_encrypt` — decrypt an upper-cased hex string → JSON dict."""
    ciphertext = bytes.fromhex(data)
    decryptor = Cipher(algorithms.AES(key.encode("utf-8")), modes.ECB()).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    raw = unpadder.update(padded) + unpadder.finalize()
    # ``json.loads`` returns ``Any``; the encrypted envelope always decodes to a JSON
    # object per iSolarCloud's contract, so narrow the return type for downstream typing.
    decoded: dict[str, Any] = json.loads(raw.decode("utf-8"))
    return decoded


def _rsa_encrypt(value: str, public_key_pem: str) -> str:
    """RSA (PKCS#1 v1.5) encrypt a short string with the login public key → base64."""
    public_key = load_pem_public_key(public_key_pem.encode("utf-8"))
    # ``load_pem_public_key`` returns a union that includes types without ``.encrypt``
    # in some ``cryptography`` versions (older stubs) and a concrete RSAPublicKey in
    # others (newer stubs). ``unused-ignore`` prevents mypy from complaining on the
    # newer-stub side where the ``union-attr`` ignore is unnecessary.
    ciphertext = public_key.encrypt(value.encode("utf-8"), asym_padding.PKCS1v15())  # type: ignore[union-attr,unused-ignore]
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
        sys_code: str = SYS_CODE,
        lang: str = "_en_US",
    ) -> None:
        """Initialise the user-account auth.

        If ``websession`` is not supplied, an owned session with a request timeout is
        created and closed by :meth:`async_close` / ``async with`` exit.

        ``sys_code`` selects the client surface the ``sys_code`` header advertises:
        ``"200"`` (the default) is the **web** client; the phone app sends ``"900"``.
        Both are accepted, but the two surfaces can return different field sets, so pass
        ``sys_code="900"`` to request the app surface (#91).
        """
        self.host = host.value if isinstance(host, Server) else host
        self._email = email
        self._password = password
        self.app_key = app_key
        self.access_key = access_key
        self.public_key_pem = public_key_pem
        self.sys_code = sys_code
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
            "sys_code": self.sys_code,
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

    async def async_request_soft(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make an authenticated request and return the envelope even on API failure.

        Re-logs in once when the token is rejected (same codes as :meth:`async_request`).
        Does **not** raise on non-success ``result_code`` — useful for probing unknown
        endpoints (sungrow-hass #271). Network/HTTP errors still propagate from aiohttp.
        """
        await self.async_get_token()
        payload = {
            **self._common(),
            "user_id": self.user_id,
            "token": self.token,
            "lang": self.lang,
            **(body or {}),
        }
        data = await self._post(path, payload, user_id=self.user_id or "")
        if not self._succeeded(data) and str(data.get("result_code")) in _LOGIN_INVALID_CODES:
            _LOGGER.debug("iSolarCloud token rejected (%s); re-logging in", data.get("result_code"))
            self.token = None
            await self.async_get_token()
            payload["user_id"] = self.user_id
            payload["token"] = self.token
            data = await self._post(path, payload, user_id=self.user_id or "")
        return data

    async def async_request(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make an authenticated request, re-logging in once if the token is rejected."""
        data = await self.async_request_soft(path, body)
        if not self._succeeded(data):
            raise PySolarCloudException.from_response(data)
        return data

    async def async_get_plants(self) -> list[dict[str, Any]]:
        """Return the plants on the account (minimal read to prove the client, #40)."""
        data = await self.async_request(_PLANT_LIST_PATH, {"valid_flag": "1,3"})
        result = data.get("result_data") or {}
        page_list = result.get("pageList")
        return list(page_list) if isinstance(page_list, list) else []

    async def async_get_plant_detail(self, ps_id: str | int) -> dict[str, Any]:
        """Return a plant's realtime household detail payload, for the app/web API (#90).

        Posts to ``getPsDetailWithPsType`` — the path the app's realtime household
        dashboard (``getPsDetailBalcony`` / ``getPsDetailOversea``) actually uses — with
        ``is_get_ps_level_data=1``, ``version_tag=1``, ``is_shut_down_flag=1`` and
        ``is_get_hm_info=1``. Non-China regions additionally send ``func_code=5`` (the
        ``getPsDetailOversea`` variant). This replaces the previous ``getPsDetail`` call
        that mis-sent ``valid_flag`` (a ``getPsList`` parameter), the likely cause of the
        unit-less realtime fields consumers had to work around.

        Returns the raw ``result_data`` dict (e.g. ``curr_power`` and other plant-level
        fields). The exact field set is model/region-dependent; consumers map it onto the
        measure-point model (KRoperUK/sungrow-hass#269).
        """
        body: dict[str, Any] = {
            "ps_id": str(ps_id),
            "is_get_ps_level_data": "1",
            "version_tag": "1",
            "is_shut_down_flag": "1",
            "is_get_hm_info": "1",
        }
        # China (``gateway.isolarcloud.com``) uses the domestic variant, which omits
        # ``func_code``; every other region mirrors ``getPsDetailOversea`` and adds
        # ``func_code=5``.
        if self.host != Server.China.value:
            body["func_code"] = "5"
        data = await self.async_request(_PLANT_DETAIL_WITH_TYPE_PATH, body)
        return dict(data.get("result_data") or {})

    async def async_get_plant_detail_daily(self, ps_id: str | int, date_id: str) -> dict[str, Any]:
        """Return a plant's daily-detail payload via ``getPsDetail(ps_id, date_id)`` (#90).

        The app's ``getPsDetail`` builder takes ``ps_id`` plus a ``date_id`` (``YYYYMMDD``)
        and serves the daily/history detail view — distinct from the realtime household
        view :meth:`async_get_plant_detail` now uses. Returns the raw ``result_data`` dict.
        """
        data = await self.async_request(_PLANT_DETAIL_PATH, {"ps_id": str(ps_id), "date_id": str(date_id)})
        return dict(data.get("result_data") or {})

    async def async_get_devices(self, ps_id: str | int) -> list[dict[str, Any]]:
        """Return the devices for a plant (app/web equivalent of getDeviceListByPsId, #53).

        Returns a list of device dicts, each containing at minimum ``device_type``,
        ``uuid``, ``device_name``, ``device_model_code``, and ``device_sn``. The exact
        fields vary by region/firmware.
        """
        data = await self.async_request(_DEVICE_LIST_PATH, {"ps_id": str(ps_id)})
        result = data.get("result_data") or {}
        page_list = result.get("pageList")
        if isinstance(page_list, list):
            return list(page_list)
        # Some regions nest devices directly in result_data as a list.
        if isinstance(result, list):
            return list(result)
        return []

    async def async_get_device_realtime(
        self, ps_key: str | list[str], *, point_ids: list[str] | None = None
    ) -> dict[str, dict[str, Any]]:
        """Return per-device realtime data keyed by device uuid (#89).

        Posts to ``queryDeviceRealTimeDataByPsKeys`` — the app's canonical per-device
        realtime path, keyed by ``ps_key`` — using the app's request shape
        ``{ps_key_list, point_id_list, is_get_point_dict}``. This replaces the previous
        ``{ps_id, sn, points}`` call to ``/v1/devService/queryDevice``, a path that does
        not exist in the app.

        ``ps_key`` is a single device ``ps_key`` (from :meth:`async_get_devices`) or a
        list of them. ``point_ids`` optionally restricts the points requested; when
        omitted the server returns its default set.

        Returns ``{uuid: {point_id: {"id", "value", "unit", "name"}}}``, mirroring the
        ``point_dict`` / ``device_point`` / ``p<id>`` parsing of
        :meth:`Plants.async_get_device_realtime`.

        .. note::
            The app registers this path in ``AppUrlPath.java`` but builds its body
            dynamically, so the exact request field names (``ps_key_list``,
            ``point_id_list``, ``is_get_point_dict``) are taken from the sibling OpenAPI
            ``getDeviceRealTimeData`` shape rather than a named app builder, and are
            **unverified against a live device**.
        """
        ps_key_list = [ps_key] if isinstance(ps_key, str) else [str(k) for k in ps_key]
        body: dict[str, Any] = {"ps_key_list": ps_key_list, "is_get_point_dict": "1"}
        if point_ids:
            body["point_id_list"] = [str(pid) for pid in point_ids]
        data = await self.async_request(_DEVICE_REALTIME_PATH, body)
        result = data.get("result_data") or {}
        point_dict_items = result.get("point_dict") or []
        point_dict = {str(p["point_id"]): p for p in point_dict_items if isinstance(p, dict) and "point_id" in p}
        out: dict[str, dict[str, Any]] = {}
        device_list = result.get("device_point_list") or []
        for entry in device_list:
            # getDeviceRealTimeData-style responses nest the device fields (uuid + p<id>
            # values) under a "device_point" key; others put them at the top level.
            device = entry.get("device_point", entry) if isinstance(entry, dict) else entry
            if not isinstance(device, dict):
                continue
            uuid = str(device.get("uuid") or device.get("device_id") or "")
            if not uuid:
                continue
            points = {
                k[1:]: self._format_point(k[1:], v, point_dict)
                for k, v in device.items()
                if k[0] == "p" and k[1:].isdigit()
            }
            out.setdefault(uuid, {}).update(points)
        return out

    @staticmethod
    def _format_point(point_id: str, point_value: Any, point_dict: dict[str, Any]) -> dict[str, Any]:
        """Normalise a single ``p<id>`` value into ``{id, value, unit, name}``.

        Mirrors :meth:`Plants._format_measure_point` minus the code map (the user API has
        no static measure-point name table), coercing numeric strings to ``float`` and
        pulling ``point_unit`` / ``point_name`` from the response ``point_dict``.
        """
        v: float | str | None
        try:
            v = float(point_value) if point_value is not None else None
        except (TypeError, ValueError):
            v = point_value
        meta = point_dict.get(point_id, {})
        return {
            "id": point_id,
            "value": v,
            "unit": meta.get("point_unit"),
            "name": meta.get("point_name"),
        }

    async def async_get_historical_data(
        self,
        ps_id: str | int,
        *,
        point_ids: list[str],
        start_time: str,
        end_time: str,
        interval: int = 5,
    ) -> list[dict[str, Any]]:
        """Return historical minute-level data for specified points (#53).

        ``start_time`` / ``end_time``: ``"YYYYMMDDHHmmss"`` format.
        ``interval``: minutes between samples (default 5, the iSolarCloud update cadence).

        Returns a list of time-series rows, each ``{time_stamp, p<id>: value, ...}``.
        """
        body: dict[str, Any] = {
            "ps_id": str(ps_id),
            "points": ",".join(f"p{pid}" for pid in point_ids),
            "start_time_stamp": start_time,
            "end_time_stamp": end_time,
            "minute_interval": str(interval),
        }
        data = await self.async_request(_HISTORICAL_DATA_PATH, body)
        result = data.get("result_data")
        if isinstance(result, list):
            return list(result)
        # Some responses nest the series under the ps_id key.
        if isinstance(result, dict):
            series = result.get(str(ps_id))
            if isinstance(series, list):
                return series
        return []
