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

# EV charger ("charging pile") read paths (all ``/v1/devService/``; #93). The
# charging-pile devService calls use the app's camelCase ``psId`` parameter (not the
# ``ps_id`` of the powerStationService calls), and ``getChargingPileRealData`` /
# ``getChargingPileLastData`` take ``uuid`` as an **integer** on the wire.
_CHARGING_PILE_LIST_PATH = "/v1/devService/getChargingPileList"
_CHARGING_PILE_REAL_DATA_PATH = "/v1/devService/getChargingPileRealData"
_CHARGING_PILE_LAST_DATA_PATH = "/v1/devService/getChargingPileLastData"
_CHARGE_PILE_OVERVIEW_PATH = "/v1/devService/getChargePileOverviewInfo"
_CHARGING_PILE_PROPERTY_PATH = "/v1/devService/getChargingPileProperty"

# Battery capacity & SoC read paths (#94). ``getBatteryCapacityByPsIdV2``,
# ``querySocByPsId`` and ``querySocBySn`` live under ``/v1/devService/`` and take the
# ``ps_id`` / ``bt_sn`` params of the app builders; ``getPsBatteryInfo`` lives under
# ``/v1/powerStationService/`` and additionally takes ``query_type`` + ``date_id``.
_BATTERY_CAPACITY_PATH = "/v1/devService/getBatteryCapacityByPsIdV2"
_BATTERY_INFO_PATH = "/v1/powerStationService/getPsBatteryInfo"
_SOC_BY_PS_ID_PATH = "/v1/devService/querySocByPsId"
_SOC_BY_SN_PATH = "/v1/devService/querySocBySn"

# Fault / alarm READ paths (#96). All under ``/v1/faultService/`` in the app's
# ``HttpRequest.java`` fault builders. Read-only; fault acknowledgement / repair writes
# are intentionally out of scope.
_FAULT_COUNT_BY_PS_PATH = "/v1/faultService/getDevFaultCountByPsId"
_FAULT_LIST_PATH = "/v1/faultService/queryFaultList"
_FAULT_DETAIL_PATH = "/v1/faultService/getFaultDetail"
_PS_OPEN_FAULT_NUM_PATH = "/v1/faultService/getPsOpenFaultNum"
_NOT_READ_FAULT_COUNT_PATH = "/v1/faultService/getNotReadFaultCount"

# Aggregate energy / report READ paths (#97), both under ``/v1/powerStationService/``.
_HOUSEHOLD_STORAGE_REPORT_PATH = "/v1/powerStationService/getHouseholdStoragePsReport"
_PS_ENERGY_SUMMARY_PATH = "/v1/powerStationService/getPsEnergySummaryInfo"

# Per-device history READ paths (#98), both under ``/v1/commonService/``. The
# day/month/year path is the app's **plural** ``queryDevicePointsDayMonthYearDataList``
# builder (fully parameterised in ``HttpRequest.java``); the app also registers a
# *singular* ``queryDevicePointDayMonthYearDataList`` path with no named builder, so the
# verified plural builder is used here (see :meth:`async_get_device_day_month_year_history`).
_DEVICE_DMY_HISTORY_PATH = "/v1/commonService/queryDevicePointsDayMonthYearDataList"
_DEVICE_MINUTE_HISTORY_PATH = "/v1/commonService/queryDevicePointMinuteDataList"

# --- App-native scheduling & home-operation-mode endpoints (#95) ---------
#
# The app drives charge/discharge *scheduling* and the home (energy-management)
# operation mode through these builders, rather than hand-rolling the raw
# 10003/10004/10005 dispatch parameter writes. Verified against the app's
# ``HttpRequest.java``.
_HOME_SETTING_DETAIL_PATH = "/v1/devService/getHomeSettingDetail"
_OPERATION_MODE_PATH = "/v1/devService/paramSetHomeSettingOperationMode"
_DISCHARGE_PLAN_SAVE_PATH = "/v1/devService/addOrUpdateDischargePlan"
_DISCHARGE_PLAN_DELETE_PATH = "/v1/devService/deleteDischargePlan"
_DISCHARGE_PLAN_SELECT_PATH = "/v1/devService/selectDischargePlan"
_DISCHARGE_TEMPLATE_PATH = "/v1/devService/getDischargeTemplateInfo"
_SYS_POWER_BACKUP_PATH = "/v1/devService/setSysPowerBackupParam"

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

    # --- EV charger ("charging pile") reads (#93) ---------------------------
    #
    # Read-only helpers for the app's dedicated charging-pile API. EV chargers never
    # surface through the plant realtime endpoint, so these are the only way to enumerate
    # and read chargers on the user transport. Control writes (``sendChargingPileCommand``)
    # are intentionally out of scope here. Verified against the app's ``HttpRequest.java``
    # ``getChargingPile*`` / ``getChargePileOverviewInfo`` builders.

    async def async_get_charging_piles(self, ps_id: str | int) -> list[dict[str, Any]]:
        """List the EV chargers (charging piles) for a plant (``getChargingPileList``, #93).

        Sends ``psId`` (the app's camelCase parameter). Returns the list of charger dicts
        (a ``pageList`` or a bare list, depending on region); each entry typically carries
        the charger ``uuid``, name and model. Returns ``[]`` when the plant has no chargers.
        """
        data = await self.async_request(_CHARGING_PILE_LIST_PATH, {"psId": str(ps_id)})
        result = data.get("result_data")
        if isinstance(result, list):
            return list(result)
        if isinstance(result, dict):
            page_list = result.get("pageList")
            if isinstance(page_list, list):
                return list(page_list)
        return []

    async def async_get_charging_pile_realtime(self, uuid: int | str) -> dict[str, Any]:
        """Return realtime data for one charger (``getChargingPileRealData``, #93).

        ``uuid`` is the charger's integer id; the app sends it as an integer on the wire,
        so it is coerced to ``int`` here (a non-numeric ``uuid`` raises ``ValueError``).
        Returns the raw ``result_data`` dict (charge power, session energy, connector
        state, etc.; the exact fields are model/region-dependent).
        """
        data = await self.async_request(_CHARGING_PILE_REAL_DATA_PATH, {"uuid": int(uuid)})
        return dict(data.get("result_data") or {})

    async def async_get_charging_pile_last_data(self, uuid: int | str) -> dict[str, Any]:
        """Return the last-known data for one charger (``getChargingPileLastData``, #93).

        Like :meth:`async_get_charging_pile_realtime`, ``uuid`` is sent as an integer.
        Returns the raw ``result_data`` dict.
        """
        data = await self.async_request(_CHARGING_PILE_LAST_DATA_PATH, {"uuid": int(uuid)})
        return dict(data.get("result_data") or {})

    async def async_get_charge_pile_overview(self, ps_id: str | int) -> dict[str, Any]:
        """Return the plant-level charger overview (``getChargePileOverviewInfo``, #93).

        Sends ``psId``. Returns the raw ``result_data`` dict (aggregate charger counts /
        status for the plant).
        """
        data = await self.async_request(_CHARGE_PILE_OVERVIEW_PATH, {"psId": str(ps_id)})
        return dict(data.get("result_data") or {})

    async def async_get_charging_pile_property(self, uuid: int | str, point_id: str | int) -> Any:
        """Return a single charger property point (``getChargingPileProperty``, #93).

        The app's ``getChargingPileProperty`` builder passes ``uuid`` as a **string** here
        (unlike the realtime/last-data calls), plus a ``point_id``. Returns the raw
        ``result_data`` value verbatim — its shape is point-dependent and is
        **unverified against a live device**, so no assumptions are made about it.
        """
        data = await self.async_request(_CHARGING_PILE_PROPERTY_PATH, {"uuid": str(uuid), "point_id": str(point_id)})
        return data.get("result_data")

    # --- Battery capacity & SoC reads (#94) ---------------------------------
    #
    # Read-only helpers exposing the app's real battery nameplate capacity and SoC, so
    # consumers can size charge/discharge power ceilings per device instead of a static
    # cap. Verified against the app's ``HttpRequest.java`` builders.

    async def async_get_battery_capacity(self, ps_id: str | int) -> dict[str, Any]:
        """Return real battery nameplate capacity for a plant (``getBatteryCapacityByPsIdV2``, #94).

        Sends ``ps_id``. Returns the raw ``result_data`` dict (nameplate/usable capacity;
        the exact fields are model/region-dependent).
        """
        data = await self.async_request(_BATTERY_CAPACITY_PATH, {"ps_id": str(ps_id)})
        return dict(data.get("result_data") or {})

    async def async_get_battery_info(
        self,
        ps_id: str | int,
        *,
        query_type: str | int | None = None,
        date_id: str | None = None,
        minute_interval: str | int | None = None,
    ) -> dict[str, Any]:
        """Return the plant battery info block (``getPsBatteryInfo``, #94).

        Sends ``ps_id`` and, when provided, ``query_type``, ``date_id`` and
        ``minute_interval``. Returns the raw ``result_data`` dict.

        .. note::
            The app's ``getPsBatteryInfo`` builder always sends ``query_type`` and
            ``date_id`` alongside ``ps_id`` (with an optional ``minute_interval``). The
            **parameter names** are verified against the app, but their accepted **values**
            (which ``query_type`` selects which block; the ``date_id`` format) are
            **unverified against a live device**, so they are left to the caller and only
            included when supplied.
        """
        body: dict[str, Any] = {"ps_id": str(ps_id)}
        if query_type is not None:
            body["query_type"] = str(query_type)
        if date_id is not None:
            body["date_id"] = str(date_id)
        if minute_interval is not None:
            body["minute_interval"] = str(minute_interval)
        data = await self.async_request(_BATTERY_INFO_PATH, body)
        return dict(data.get("result_data") or {})

    async def async_get_soc_by_ps_id(self, ps_id: str | int) -> dict[str, Any]:
        """Return battery SoC for a plant (``querySocByPsId``, #94).

        Sends ``ps_id``. Returns the raw ``result_data`` dict (state of charge; the exact
        fields are model/region-dependent).
        """
        data = await self.async_request(_SOC_BY_PS_ID_PATH, {"ps_id": str(ps_id)})
        return dict(data.get("result_data") or {})

    async def async_get_soc_by_sn(self, bt_sn: str) -> dict[str, Any]:
        """Return battery SoC for a specific battery serial (``querySocBySn``, #94).

        Sends ``bt_sn`` (the app's battery-serial parameter name). Returns the raw
        ``result_data`` dict.
        """
        data = await self.async_request(_SOC_BY_SN_PATH, {"bt_sn": str(bt_sn)})
        return dict(data.get("result_data") or {})

    # --- Fault / alarm reads (#96) ------------------------------------------
    #
    # Read-only helpers over the app's ``/v1/faultService/`` API. Fault
    # acknowledgement / repair-order writes are intentionally out of scope. Verified
    # against the app's ``HttpRequest.java`` fault builders.

    async def async_get_fault_count(self, ps_id: str | int) -> dict[str, Any]:
        """Return the device fault count for a plant (``getDevFaultCountByPsId``, #96).

        Sends ``ps_id``. Returns the raw ``result_data`` dict (per-type fault counts; the
        exact fields are model/region-dependent).
        """
        data = await self.async_request(_FAULT_COUNT_BY_PS_PATH, {"ps_id": str(ps_id)})
        return dict(data.get("result_data") or {})

    async def async_get_fault_detail(self, fault_code: str | int) -> dict[str, Any]:
        """Return the detail for a single fault (``getFaultDetail``, #96).

        Sends ``fault_code`` (the app's parameter name). Returns the raw ``result_data``
        dict.
        """
        data = await self.async_request(_FAULT_DETAIL_PATH, {"fault_code": str(fault_code)})
        return dict(data.get("result_data") or {})

    async def async_query_faults(
        self,
        ps_id: str | int | None = None,
        *,
        ps_key: str | None = None,
        uuid: str | None = None,
        process_status: str | int | None = None,
        fault_type: str | int | None = None,
        fault_type_code: str | int | None = None,
        share_type: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        sort_column: str | None = None,
        sort_type: str | None = None,
        fault_name_like: str | None = None,
        cur_page: int = 1,
        size: int = 10,
    ) -> list[dict[str, Any]]:
        """Return a page of faults for the account/plant (``queryFaultList``, #96).

        Maps optional filters onto the app's ``queryFaultList`` parameters — note the
        app's camelCase ``startTime`` / ``endTime`` / ``curPage`` on the wire. ``user_id``
        is injected by :meth:`async_request`. Only supplied filters are sent. Returns the
        ``pageList`` (or a bare list, depending on region), or ``[]`` when empty.

        .. note::
            The **parameter names** are verified against the app's ``HttpRequest.java``
            builder, but the accepted **values** of ``process_status``, ``fault_type``,
            ``fault_type_code``, ``share_type``, ``sort_column`` and ``sort_type`` (which
            are app-internal enums) and the ``startTime`` / ``endTime`` formats are
            **unverified against a live device**, so they are left entirely to the caller.
        """
        body: dict[str, Any] = {"curPage": int(cur_page), "size": int(size)}
        if ps_id is not None:
            body["ps_id"] = str(ps_id)
        if ps_key is not None:
            body["ps_key"] = str(ps_key)
        if uuid is not None:
            body["uuid"] = str(uuid)
        if process_status is not None:
            body["process_status"] = str(process_status)
        if fault_type is not None:
            body["fault_type"] = str(fault_type)
        if fault_type_code is not None:
            body["fault_type_code"] = str(fault_type_code)
        if share_type is not None:
            body["share_type"] = str(share_type)
        if start_time is not None:
            body["startTime"] = str(start_time)
        if end_time is not None:
            body["endTime"] = str(end_time)
        if sort_column is not None:
            body["sort_column"] = str(sort_column)
        if sort_type is not None:
            body["sort_type"] = str(sort_type)
        if fault_name_like is not None:
            body["fault_name_like"] = str(fault_name_like)
        data = await self.async_request(_FAULT_LIST_PATH, body)
        result = data.get("result_data")
        if isinstance(result, list):
            return list(result)
        if isinstance(result, dict):
            page_list = result.get("pageList")
            if isinstance(page_list, list):
                return list(page_list)
        return []

    async def async_get_open_fault_num(self) -> dict[str, Any]:
        """Return the count of open faults for the account (``getPsOpenFaultNum``, #96).

        Sends the app's constant ``share_type="0,1,2"`` (owner/shared/authorised) and
        takes no caller parameters. Returns the raw ``result_data`` dict.
        """
        data = await self.async_request(_PS_OPEN_FAULT_NUM_PATH, {"share_type": "0,1,2"})
        return dict(data.get("result_data") or {})

    async def async_get_unread_fault_count(
        self,
        *,
        type: str | int | None = None,
        ps_id: str | int | None = None,
        uuid: str | None = None,
    ) -> dict[str, Any]:
        """Return the unread-fault count (``getNotReadFaultCount``, #96).

        Sends the app's ``type`` / ``ps_id`` / ``uuid`` parameters when supplied. Returns
        the raw ``result_data`` dict.

        .. note::
            The **parameter names** are verified against the app, but the accepted
            **values** of ``type`` (the app passes e.g. ``"3"``, whose meaning is
            undocumented) are **unverified against a live device** and left to the caller.
        """
        body: dict[str, Any] = {}
        if type is not None:
            body["type"] = str(type)
        if ps_id is not None:
            body["ps_id"] = str(ps_id)
        if uuid is not None:
            body["uuid"] = str(uuid)
        data = await self.async_request(_NOT_READ_FAULT_COUNT_PATH, body)
        return dict(data.get("result_data") or {})

    # --- Aggregate energy / report reads (#97) ------------------------------
    #
    # Read-only helpers over the app's household-storage report and energy-summary
    # endpoints. Verified against the app's ``HttpRequest.java`` builders.

    async def async_get_household_storage_report(
        self,
        ps_id: str | int,
        *,
        date_type: str | int | None = None,
        date_id: str | None = None,
        minute_interval: str | int | None = None,
    ) -> dict[str, Any]:
        """Return the household-storage energy report (``getHouseholdStoragePsReport``, #97).

        Sends ``ps_id`` and the app's constant ``version_tag="1"``, plus ``date_type``,
        ``date_id`` and ``minute_interval`` when supplied. Returns the raw ``result_data``
        dict (period energy totals / series).

        .. note::
            The **parameter names** are verified against the app, but the accepted
            **values** of ``date_type`` (day/month/year selector) and the ``date_id``
            format are **unverified against a live device**, so they are left to the
            caller and only sent when supplied.
        """
        body: dict[str, Any] = {"ps_id": str(ps_id), "version_tag": "1"}
        if date_type is not None:
            body["date_type"] = str(date_type)
        if date_id is not None:
            body["date_id"] = str(date_id)
        if minute_interval is not None:
            body["minute_interval"] = str(minute_interval)
        data = await self.async_request(_HOUSEHOLD_STORAGE_REPORT_PATH, body)
        return dict(data.get("result_data") or {})

    async def async_get_energy_summary(
        self,
        ps_id: str | int,
        *,
        date_type: str | int | None = None,
        date_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the plant energy summary (``getPsEnergySummaryInfo``, #97).

        Sends ``ps_id`` plus ``date_type`` and ``date_id`` when supplied. Returns the raw
        ``result_data`` dict.

        .. note::
            The **parameter names** are verified against the app, but the accepted
            **values** of ``date_type`` and the ``date_id`` format are **unverified
            against a live device**, so they are left to the caller and only sent when
            supplied.
        """
        body: dict[str, Any] = {"ps_id": str(ps_id)}
        if date_type is not None:
            body["date_type"] = str(date_type)
        if date_id is not None:
            body["date_id"] = str(date_id)
        data = await self.async_request(_PS_ENERGY_SUMMARY_PATH, body)
        return dict(data.get("result_data") or {})

    # --- Per-device day/month/year & minute history reads (#98) -------------
    #
    # Read-only per-device time-series helpers keyed by ``ps_key``. Verified against the
    # app's ``HttpRequest.java`` ``queryDevicePointsDayMonthYearDataList`` and
    # ``queryDevicePointMinuteDataList`` builders.

    async def async_get_device_day_month_year_history(
        self,
        ps_key: str,
        *,
        data_point: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        data_type: str | int | None = None,
        order: str | int | None = None,
        query_type: str | int | None = None,
        is_get_point_info: bool = False,
    ) -> Any:
        """Return per-device day/month/year aggregated history (#98).

        Posts to ``queryDevicePointsDayMonthYearDataList`` — the app's **plural** builder,
        which is fully parameterised in ``HttpRequest.java`` with ``ps_key``,
        ``data_point``, ``start_time``, ``end_time``, ``data_type``, ``order``,
        ``query_type`` and an optional ``is_get_point_info=1``. Optional args are sent only
        when supplied. Returns the raw ``result_data`` value verbatim.

        .. note::
            The app *also* registers a **singular** ``queryDevicePointDayMonthYearDataList``
            path with no named builder; this helper uses the verified plural builder
            instead. The **parameter names** are verified, but the accepted **values** of
            ``data_type`` (day/month/year), ``order`` and ``query_type``, and the
            ``start_time`` / ``end_time`` formats, are **unverified against a live device**
            and are left to the caller. The response shape is likewise unverified, so the
            raw ``result_data`` is returned without normalisation.
        """
        body: dict[str, Any] = {"ps_key": str(ps_key)}
        if data_point is not None:
            body["data_point"] = str(data_point)
        if start_time is not None:
            body["start_time"] = str(start_time)
        if end_time is not None:
            body["end_time"] = str(end_time)
        if data_type is not None:
            body["data_type"] = str(data_type)
        if order is not None:
            body["order"] = str(order)
        if query_type is not None:
            body["query_type"] = str(query_type)
        if is_get_point_info:
            body["is_get_point_info"] = 1
        data = await self.async_request(_DEVICE_DMY_HISTORY_PATH, body)
        return data.get("result_data")

    async def async_get_device_minute_history(
        self,
        ps_key: str,
        *,
        points: list[str],
        start_time: str,
        end_time: str,
        minute_interval: int = 5,
    ) -> Any:
        """Return per-device minute-level history (``queryDevicePointMinuteDataList``, #98).

        Posts the app's verified ``ps_key`` / ``points`` / ``start_time_stamp`` /
        ``end_time_stamp`` / ``minute_interval`` shape. ``points`` are joined with commas
        and sent verbatim (the caller supplies the exact point tokens). ``start_time`` /
        ``end_time`` are ``"YYYYMMDDHHmmss"`` timestamp strings. Returns the raw
        ``result_data`` value verbatim.

        .. note::
            The parameter names are verified against the app's ``HttpRequest.java``
            builder; the exact ``points`` token format (whether a ``p``-prefix is required)
            and the response shape are **unverified against a live device**.
        """
        body: dict[str, Any] = {
            "ps_key": str(ps_key),
            "points": ",".join(str(p) for p in points),
            "start_time_stamp": str(start_time),
            "end_time_stamp": str(end_time),
            "minute_interval": str(minute_interval),
        }
        data = await self.async_request(_DEVICE_MINUTE_HISTORY_PATH, body)
        return data.get("result_data")

    # --- App-native scheduling & home operation mode (#95) -------------------
    #
    # The app has a first-class charge/discharge *scheduling* surface, plus a friendlier
    # "home operation mode" setter, instead of hand-rolling the 10003/10004/10005
    # dispatch parameter writes that :class:`~pysolarcloud.control.Control` sends. These
    # expose that surface so consumers can offer multi-window scheduling.
    #
    # Method names, paths and request **field names** are verified against the app's
    # ``HttpRequest.java`` builders.
    #
    # .. warning::
    #     The **values** are not pinned down. ``homeSettingType``,
    #     ``energyManagementModel``, ``cycleType``, ``dischargeOption``, ``weekDays``
    #     and the ``weeklyPlan`` payloads are all UI-driven in the app (pickers, not
    #     constants), and are **unverified against a live device**. They are passed
    #     through verbatim rather than guessed at, so a caller can supply whatever the
    #     device accepts. Only the *shape* of the request is asserted here.

    async def async_get_home_setting(self, ps_id: str | int, home_setting_type: str | int = "1") -> dict[str, Any]:
        """Read a plant's home (energy-management) settings (``getHomeSettingDetail``, #95).

        Sends ``psId`` and ``homeSettingType``. Returns the raw ``result_data`` dict —
        typically the current operation mode plus backup/reserve settings and weekly
        plans, though the exact field set is model/region-dependent. ``homeSettingType``
        defaults to ``"1"``, the value the app's household view model sends; its balcony
        / micro-storage screen sends ``"3,4"``.
        """
        body = {"psId": str(ps_id), "homeSettingType": str(home_setting_type)}
        data = await self.async_request(_HOME_SETTING_DETAIL_PATH, body)
        return dict(data.get("result_data") or {})

    async def async_set_operation_mode(
        self,
        ps_id: str | int,
        *,
        uuid: str | int,
        device_type: str | int,
        energy_management_model: str | int,
        sn: str | None = None,
        weekly_plan1: Any = None,
        weekly_plan2: Any = None,
    ) -> dict[str, Any]:
        """Set a device's home operation mode (``paramSetHomeSettingOperationMode``, #95).

        Mirrors the app's ``saveOperationMode`` builder: ``psId``, ``uuid``,
        ``deviceType``, ``sn``, ``energyManagementModel`` and up to two weekly-plan
        objects. ``energy_management_model`` is passed through verbatim — the app takes
        it from a UI picker, so the accepted values are **unverified** and deliberately
        not enumerated here. ``sn``, ``weekly_plan1`` and ``weekly_plan2`` are omitted
        from the request when ``None``.

        Returns the raw ``result_data`` dict.
        """
        body: dict[str, Any] = {
            "psId": str(ps_id),
            "uuid": str(uuid),
            "deviceType": str(device_type),
            "energyManagementModel": str(energy_management_model),
        }
        if sn is not None:
            body["sn"] = str(sn)
        if weekly_plan1 is not None:
            body["weeklyPlan1"] = weekly_plan1
        if weekly_plan2 is not None:
            body["weeklyPlan2"] = weekly_plan2
        data = await self.async_request(_OPERATION_MODE_PATH, body)
        return dict(data.get("result_data") or {})

    async def async_get_discharge_template_info(
        self, ps_id: str | int, fast_discharging_uuid: str | int
    ) -> dict[str, Any]:
        """Read the discharge-plan template (``getDischargeTemplateInfo``, #95).

        Sends ``psId`` and ``fastDischargingUuid`` (the inverter's device uuid) — both
        are always supplied by the app's builder. Returns the raw ``result_data`` dict.
        """
        body = {"psId": str(ps_id), "fastDischargingUuid": str(fast_discharging_uuid)}
        data = await self.async_request(_DISCHARGE_TEMPLATE_PATH, body)
        return dict(data.get("result_data") or {})

    async def async_add_or_update_discharge_plan(
        self,
        ps_id: str | int,
        *,
        week_days: str | int,
        cycle_type: str | int,
        start_plan_time: str,
        discharge_option: str | int,
        duration: str | int,
        power_value: str | int,
    ) -> dict[str, Any]:
        """Create or update a charge/discharge plan (``addOrUpdateDischargePlan``, #95).

        Field-for-field with the app's builder: ``psId``, ``weekDays``, ``cycleType``,
        ``startPlanTime``, ``dischargeOption``, ``duration``, ``powerValue``. Every value
        is sent verbatim as a string (the app derives them from its schedule UI, so their
        accepted encodings are **unverified against a live device**).

        Returns the raw ``result_data`` dict.
        """
        body = {
            "psId": str(ps_id),
            "weekDays": str(week_days),
            "cycleType": str(cycle_type),
            "startPlanTime": str(start_plan_time),
            "dischargeOption": str(discharge_option),
            "duration": str(duration),
            "powerValue": str(power_value),
        }
        data = await self.async_request(_DISCHARGE_PLAN_SAVE_PATH, body)
        return dict(data.get("result_data") or {})

    async def async_delete_discharge_plan(self, ps_id: str | int) -> dict[str, Any]:
        """Delete the plant's discharge plan (``deleteDischargePlan``, #95).

        The app's builder sends only ``psId`` — there is no per-plan id in the request,
        so this acts on the plant's plan as a whole. Returns the raw ``result_data`` dict.
        """
        data = await self.async_request(_DISCHARGE_PLAN_DELETE_PATH, {"psId": str(ps_id)})
        return dict(data.get("result_data") or {})

    async def async_select_discharge_plan(self, ps_id: str | int) -> dict[str, Any]:
        """Activate the plant's discharge plan (``selectDischargePlan``, #95).

        Like :meth:`async_delete_discharge_plan`, the app's builder sends only ``psId``.
        Returns the raw ``result_data`` dict.
        """
        data = await self.async_request(_DISCHARGE_PLAN_SELECT_PATH, {"psId": str(ps_id)})
        return dict(data.get("result_data") or {})

    async def async_set_system_power_backup_param(
        self,
        ps_id: str | int,
        *,
        task_name: str,
        mode: str | int,
        status: str | int,
        config: dict[str, str],
    ) -> dict[str, Any]:
        """Set the system power-backup (reserve) parameters (``setSysPowerBackupParam``, #95).

        The app's builder sends ``psId``, ``taskName``, ``mode``, ``status`` and a
        ``config`` map (the Java constants resolve to the literal key ``"config"``).
        ``config`` is passed through verbatim — its keys and values are **unverified
        against a live device**. Returns the raw ``result_data`` dict.
        """
        body = {
            "psId": str(ps_id),
            "taskName": str(task_name),
            "mode": str(mode),
            "status": str(status),
            "config": config,
        }
        data = await self.async_request(_SYS_POWER_BACKUP_PATH, body)
        return dict(data.get("result_data") or {})
