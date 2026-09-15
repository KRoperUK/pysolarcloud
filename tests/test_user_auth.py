"""Tests for the user-account (app/web) login client (#40)."""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from pysolarcloud import PySolarCloudException, Server, UserAuth
from pysolarcloud import user_auth as ua


def _auth(session=None) -> UserAuth:
    return UserAuth(Server.Europe, "me@example.com", "secret", websession=session or MagicMock())


# --- crypto helpers ---------------------------------------------------------


def test_aes_round_trip():
    """AES-128-ECB encrypt/decrypt is a faithful round-trip for a JSON payload."""
    key = "web0123456789abc"  # 16 chars
    payload = {"user_account": "x", "nested": {"a": 1, "b": [1, 2, 3]}}
    assert ua._aes_decrypt(ua._aes_encrypt(payload, key), key) == payload


def test_aes_output_is_upper_hex():
    """The ciphertext is upper-cased hex (the wire format the API expects)."""
    out = ua._aes_encrypt({"a": 1}, "web0123456789abc")
    assert out == out.upper()
    bytes.fromhex(out)  # valid hex, raises if not


def test_rsa_encrypt_produces_key_sized_base64():
    """RSA(PKCS1v15) with the 1024-bit login key yields 128 bytes, base64-encoded."""
    out = ua._rsa_encrypt("web0123456789abc", ua.PUBLIC_KEY_PEM)
    assert len(base64.b64decode(out)) == 128
    # Non-deterministic padding: two encryptions of the same value differ.
    assert out != ua._rsa_encrypt("web0123456789abc", ua.PUBLIC_KEY_PEM)


# --- request envelope -------------------------------------------------------


async def test_post_encrypts_and_decrypts_round_trip(monkeypatch):
    """_post AES-encrypts the body, sends the auth headers, and decrypts the reply."""
    fixed_key = "web0123456789abc"
    monkeypatch.setattr(ua, "_random_aes_key", lambda: fixed_key)

    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.text = AsyncMock(return_value=ua._aes_encrypt({"result_msg": "success"}, fixed_key))
    session = MagicMock()
    session.request = AsyncMock(return_value=resp)

    auth = _auth(session)
    out = await auth._post("/v1/userService/login", {"user_account": "x"}, user_id="")

    assert out == {"result_msg": "success"}
    args, kwargs = session.request.call_args
    assert args[0] == "post"
    assert args[1] == "https://gateway.isolarcloud.eu/v1/userService/login"
    # The body is the AES-encrypted payload, not plaintext.
    assert kwargs["data"] == ua._aes_encrypt({"user_account": "x"}, fixed_key)
    assert "user_account" not in kwargs["data"]
    headers = kwargs["headers"]
    assert headers["x-access-key"] == ua.ACCESS_KEY
    assert headers["sys_code"] == ua.SYS_CODE
    assert headers["x-random-secret-key"]  # RSA-encrypted AES key present


async def test_post_uses_custom_sys_code(monkeypatch):
    """A sys_code kwarg overrides the default web value on the wire (#91)."""
    fixed_key = "web0123456789abc"
    monkeypatch.setattr(ua, "_random_aes_key", lambda: fixed_key)

    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.text = AsyncMock(return_value=ua._aes_encrypt({"result_msg": "success"}, fixed_key))
    session = MagicMock()
    session.request = AsyncMock(return_value=resp)

    auth = UserAuth(Server.Europe, "me@example.com", "secret", websession=session, sys_code="900")
    assert auth.sys_code == "900"
    await auth._post("/v1/userService/login", {"user_account": "x"}, user_id="")

    assert session.request.call_args.kwargs["headers"]["sys_code"] == "900"


def test_default_sys_code_is_web():
    """The default sys_code is the web client value '200' (#91)."""
    assert _auth().sys_code == ua.SYS_CODE == "200"


# --- login / token ----------------------------------------------------------


async def test_login_stores_token_and_user_id():
    """A successful login stores the token and user id."""
    auth = _auth()
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {"token": "T", "user_id": "42"}})

    await auth.async_login()

    assert auth.token == "T"
    assert auth.user_id == "42"


async def test_login_failure_raises():
    """A failed login raises a typed PySolarCloudException and leaves no token."""
    auth = _auth()
    auth._post = AsyncMock(return_value={"result_code": "E00003", "result_msg": "er_token_login_invalid"})

    with pytest.raises(PySolarCloudException):
        await auth.async_login()
    assert auth.token is None


async def test_login_state_zero_raises_auth_error_with_attempts():
    """A success envelope with login_state 0 (bad creds/region) raises a typed AuthError.

    The login endpoint returns result_code "1" even for a rejected password; the real
    signal is login_state. The error must expose the remaining-attempts count so callers
    can avoid triggering a lockout (validated live: this is the real response shape).
    """
    from pysolarcloud import AuthError

    auth = _auth()
    auth._post = AsyncMock(
        return_value={
            "result_code": "1",
            "result_msg": "success",
            "result_data": {"login_state": "0", "msg": "account or password incorrect", "remain_times": 1},
        }
    )

    with pytest.raises(AuthError) as exc:
        await auth.async_login()
    assert auth.token is None
    assert "1 attempt(s) remaining" in str(exc.value.error_description)
    assert "account or password incorrect" in str(exc.value.error_description)


async def test_login_state_one_with_token_succeeds():
    """login_state 1 + a token is a real success."""
    auth = _auth()
    auth._post = AsyncMock(
        return_value={
            "result_code": "1",
            "result_msg": "success",
            "result_data": {"login_state": "1", "token": "T", "user_id": "42"},
        }
    )

    await auth.async_login()

    assert auth.token == "T"
    assert auth.user_id == "42"


async def test_get_token_logs_in_once_under_concurrency():
    """Concurrent callers trigger exactly one login (serialised)."""
    auth = _auth()
    calls = 0

    async def fake_login():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        auth.token = "T"
        auth.user_id = "42"

    auth.async_login = AsyncMock(side_effect=fake_login)

    tokens = await asyncio.gather(auth.async_get_token(), auth.async_get_token())

    assert tokens == ["T", "T"]
    assert calls == 1


async def test_valid_token_not_re_logged_in():
    """A cached token is returned without logging in again."""
    auth = _auth()
    auth.token = "cached"
    auth.async_login = AsyncMock()

    assert await auth.async_get_token() == "cached"
    auth.async_login.assert_not_called()


# --- authenticated requests -------------------------------------------------


async def test_request_injects_token_and_returns_data():
    """async_request injects user_id/token/lang and returns the decrypted payload."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {"x": 1}})

    out = await auth.async_request("/v1/foo", {"ps_id": "9"})

    body = auth._post.call_args.args[1]
    assert body["token"] == "T"
    assert body["user_id"] == "42"
    assert body["lang"] == "_en_US"
    assert body["ps_id"] == "9"
    assert out["result_data"] == {"x": 1}


async def test_request_re_logs_in_on_invalid_token():
    """A rejected token triggers one re-login and a retry."""
    auth = _auth()
    auth.token = "stale"
    auth.user_id = "42"
    responses = [
        {"result_code": "E00003", "result_msg": "er_token_login_invalid"},
        {"result_msg": "success", "result_data": {"ok": True}},
    ]
    auth._post = AsyncMock(side_effect=responses)

    async def fake_login():
        auth.token = "fresh"
        auth.user_id = "42"

    auth.async_login = AsyncMock(side_effect=fake_login)

    out = await auth.async_request("/v1/foo")

    auth.async_login.assert_awaited_once()
    assert auth._post.await_count == 2
    assert out["result_data"] == {"ok": True}


async def test_get_plants_returns_page_list():
    """async_get_plants returns the pageList from getPsList."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(
        return_value={"result_msg": "success", "result_data": {"pageList": [{"ps_id": 1}, {"ps_id": 2}]}}
    )

    plants = await auth.async_get_plants()

    assert [p["ps_id"] for p in plants] == [1, 2]


async def test_get_plants_empty_when_no_page_list():
    """A response without a pageList yields an empty list, not an error."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {}})

    assert await auth.async_get_plants() == []


async def test_get_plant_detail_uses_get_ps_detail_with_ps_type(monkeypatch):
    """async_get_plant_detail posts the getPsDetailWithPsType household params (#90)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(
        return_value={
            "result_msg": "success",
            "result_data": {"curr_power": {"value": "3200", "unit": "W"}, "ps_id": 5},
        }
    )

    detail = await auth.async_get_plant_detail(5)

    path = auth._post.call_args.args[0]
    body = auth._post.call_args.args[1]
    assert path == ua._PLANT_DETAIL_WITH_TYPE_PATH
    assert body["ps_id"] == "5"
    assert body["is_get_ps_level_data"] == "1"
    assert body["version_tag"] == "1"
    assert body["is_shut_down_flag"] == "1"
    assert body["is_get_hm_info"] == "1"
    # valid_flag was a getPsList param and must no longer be sent.
    assert "valid_flag" not in body
    assert detail["curr_power"] == {"value": "3200", "unit": "W"}


async def test_get_plant_detail_adds_func_code_for_non_china():
    """A non-China region mirrors getPsDetailOversea and adds func_code=5 (#90)."""
    auth = _auth()  # Server.Europe
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {}})

    await auth.async_get_plant_detail(5)

    assert auth._post.call_args.args[1]["func_code"] == "5"


async def test_get_plant_detail_omits_func_code_for_china():
    """China uses the domestic variant, which omits func_code (#90)."""
    auth = UserAuth(Server.China, "me@example.com", "secret", websession=MagicMock())
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {}})

    await auth.async_get_plant_detail(5)

    assert "func_code" not in auth._post.call_args.args[1]


async def test_get_plant_detail_empty_when_no_data():
    """A response without result_data yields an empty dict, not an error."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success"})

    assert await auth.async_get_plant_detail(5) == {}


async def test_get_plant_detail_daily_uses_get_ps_detail():
    """async_get_plant_detail_daily posts getPsDetail with ps_id + date_id (#90)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {"day_power": "12"}})

    detail = await auth.async_get_plant_detail_daily(5, "20260718")

    path = auth._post.call_args.args[0]
    body = auth._post.call_args.args[1]
    assert path == ua._PLANT_DETAIL_PATH
    assert body["ps_id"] == "5"
    assert body["date_id"] == "20260718"
    assert "valid_flag" not in body
    assert detail["day_power"] == "12"


# --- session lifecycle ------------------------------------------------------


async def test_async_close_closes_owned_session():
    """An internally-created session is owned and closed by async_close()."""
    auth = UserAuth(Server.Europe, "me@example.com", "secret")
    assert auth.websession.closed is False
    await auth.async_close()
    assert auth.websession.closed is True


async def test_async_close_leaves_injected_session_open():
    """An injected session is not owned, so async_close() must not close it."""
    session = MagicMock()
    session.closed = False
    session.close = AsyncMock()
    auth = UserAuth(Server.Europe, "me@example.com", "secret", websession=session)
    await auth.async_close()
    session.close.assert_not_called()


# --- async_get_devices (#53) ------------------------------------------------


async def test_get_devices_returns_page_list():
    """async_get_devices returns the pageList from the device list endpoint."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(
        return_value={
            "result_msg": "success",
            "result_data": {
                "pageList": [
                    {"uuid": "dev-1", "device_type": 14, "device_name": "Inverter"},
                    {"uuid": "dev-2", "device_type": 43, "device_name": "Battery"},
                ]
            },
        }
    )

    devices = await auth.async_get_devices("123")

    assert len(devices) == 2
    assert devices[0]["uuid"] == "dev-1"
    assert devices[1]["device_type"] == 43


async def test_get_devices_empty_when_no_devices():
    """An empty response returns an empty list."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {}})

    assert await auth.async_get_devices("123") == []


# --- async_get_device_realtime (#89) ----------------------------------------


async def test_get_device_realtime_uses_ps_keys_path_and_shape():
    """async_get_device_realtime posts ps_key_list to queryDeviceRealTimeDataByPsKeys (#89)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(
        return_value={
            "result_msg": "success",
            "result_data": {
                "point_dict": [
                    {"point_id": "13003", "point_unit": "V", "point_name": "Phase A Voltage"},
                ],
                "device_point_list": [
                    {"device_point": {"uuid": "dev-1", "p13003": "240.5", "p13004": "1.2"}},
                ],
            },
        }
    )

    result = await auth.async_get_device_realtime("dev-key-1")

    path = auth._post.call_args.args[0]
    body = auth._post.call_args.args[1]
    assert path == ua._DEVICE_REALTIME_PATH
    assert body["ps_key_list"] == ["dev-key-1"]
    assert body["is_get_point_dict"] == "1"
    # Result is keyed by device uuid, with per-point {id, value, unit, name}.
    assert result["dev-1"]["13003"] == {
        "id": "13003",
        "value": 240.5,
        "unit": "V",
        "name": "Phase A Voltage",
    }
    # A point missing from point_dict still comes back, numeric-coerced, unit/name None.
    assert result["dev-1"]["13004"] == {"id": "13004", "value": 1.2, "unit": None, "name": None}


async def test_get_device_realtime_accepts_ps_key_list_and_point_ids():
    """A list of ps_keys and explicit point_ids are forwarded as-is (#89)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {}})

    await auth.async_get_device_realtime(["k1", "k2"], point_ids=["13003", "13004"])

    body = auth._post.call_args.args[1]
    assert body["ps_key_list"] == ["k1", "k2"]
    assert body["point_id_list"] == ["13003", "13004"]


async def test_get_device_realtime_top_level_device_fields():
    """Devices whose p<id> fields sit at the top level (no device_point wrapper) parse too."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(
        return_value={
            "result_msg": "success",
            "result_data": {
                "device_point_list": [{"uuid": "dev-2", "p13003": "230"}],
            },
        }
    )

    result = await auth.async_get_device_realtime("k")

    assert result["dev-2"]["13003"]["value"] == 230.0


async def test_get_device_realtime_skips_entries_without_uuid():
    """Entries with no uuid/device_id are skipped rather than keyed under an empty string."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(
        return_value={
            "result_msg": "success",
            "result_data": {"device_point_list": [{"p13003": "1"}]},
        }
    )

    assert await auth.async_get_device_realtime("k") == {}


async def test_get_device_realtime_empty_when_no_data():
    """An empty response returns an empty dict."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {}})

    assert await auth.async_get_device_realtime("k") == {}


async def test_get_device_realtime_non_numeric_value_and_non_dict_entry():
    """Non-numeric point values pass through unchanged; non-dict list entries are skipped."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(
        return_value={
            "result_msg": "success",
            "result_data": {
                "device_point_list": [
                    "not-a-dict",
                    {"device_point": {"uuid": "dev-3", "p13010": "Running"}},
                ]
            },
        }
    )

    result = await auth.async_get_device_realtime("k")

    # The bare string entry is ignored, and a non-numeric value is kept verbatim.
    assert result == {"dev-3": {"13010": {"id": "13010", "value": "Running", "unit": None, "name": None}}}


# --- async_get_historical_data (#53) ----------------------------------------


async def test_get_historical_data_returns_series():
    """async_get_historical_data returns time-series rows."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(
        return_value={
            "result_msg": "success",
            "result_data": [
                {"time_stamp": "20260718000000", "p83033": "490"},
                {"time_stamp": "20260718000500", "p83033": "520"},
            ],
        }
    )

    rows = await auth.async_get_historical_data(
        "123",
        point_ids=["83033"],
        start_time="20260718000000",
        end_time="20260718010000",
    )

    assert len(rows) == 2
    assert rows[0]["p83033"] == "490"
    body = auth._post.call_args.args[1]
    assert body["points"] == "p83033"
    assert body["minute_interval"] == "5"


async def test_get_historical_data_nested_under_ps_id():
    """Some regions nest the series under the ps_id key in result_data."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(
        return_value={
            "result_msg": "success",
            "result_data": {
                "123": [{"time_stamp": "20260718000000", "p83033": "100"}],
                "point_dict": [{"point_id": "83033"}],
            },
        }
    )

    rows = await auth.async_get_historical_data(
        "123", point_ids=["83033"], start_time="20260718000000", end_time="20260718010000"
    )

    assert len(rows) == 1
    assert rows[0]["p83033"] == "100"


async def test_get_historical_data_empty_result():
    """An empty response returns an empty list."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": None})

    rows = await auth.async_get_historical_data(
        "123", point_ids=["83033"], start_time="20260718000000", end_time="20260718010000"
    )

    assert rows == []


# --- EV charger / charging pile (#93) ---------------------------------------


async def test_get_charging_piles_returns_page_list():
    """async_get_charging_piles returns a pageList and sends the camelCase psId."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(
        return_value={
            "result_msg": "success",
            "result_data": {"pageList": [{"uuid": 101, "device_name": "Charger A"}]},
        }
    )

    piles = await auth.async_get_charging_piles("123")

    assert auth._post.call_args.args[0] == ua._CHARGING_PILE_LIST_PATH
    assert auth._post.call_args.args[1]["psId"] == "123"
    assert piles[0]["uuid"] == 101


async def test_get_charging_piles_accepts_bare_list():
    """Some regions return the chargers as a bare list in result_data."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": [{"uuid": 1}, {"uuid": 2}]})

    piles = await auth.async_get_charging_piles("123")

    assert [p["uuid"] for p in piles] == [1, 2]


async def test_get_charging_piles_empty_when_no_data():
    """An empty response returns an empty list."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {}})

    assert await auth.async_get_charging_piles("123") == []


async def test_get_charging_pile_realtime_sends_int_uuid():
    """async_get_charging_pile_realtime coerces uuid to an int on the wire (#93)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(
        return_value={"result_msg": "success", "result_data": {"charge_power": {"value": "7.2", "unit": "kW"}}}
    )

    result = await auth.async_get_charging_pile_realtime("101")

    assert auth._post.call_args.args[0] == ua._CHARGING_PILE_REAL_DATA_PATH
    body = auth._post.call_args.args[1]
    assert body["uuid"] == 101
    assert isinstance(body["uuid"], int)
    assert result["charge_power"] == {"value": "7.2", "unit": "kW"}


async def test_get_charging_pile_realtime_empty_when_no_data():
    """A response without result_data yields an empty dict."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success"})

    assert await auth.async_get_charging_pile_realtime(101) == {}


async def test_get_charging_pile_realtime_rejects_non_numeric_uuid():
    """A non-numeric uuid raises ValueError (the app treats uuid as an integer)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {}})

    with pytest.raises(ValueError):
        await auth.async_get_charging_pile_realtime("not-an-int")


async def test_get_charging_pile_last_data_sends_int_uuid():
    """async_get_charging_pile_last_data posts an int uuid to the last-data path (#93)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {"soc": "80"}})

    result = await auth.async_get_charging_pile_last_data(101)

    assert auth._post.call_args.args[0] == ua._CHARGING_PILE_LAST_DATA_PATH
    assert auth._post.call_args.args[1]["uuid"] == 101
    assert result["soc"] == "80"


async def test_get_charge_pile_overview_sends_ps_id():
    """async_get_charge_pile_overview posts psId to the overview path (#93)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {"total_count": 2}})

    result = await auth.async_get_charge_pile_overview("123")

    assert auth._post.call_args.args[0] == ua._CHARGE_PILE_OVERVIEW_PATH
    assert auth._post.call_args.args[1]["psId"] == "123"
    assert result["total_count"] == 2


async def test_get_charging_pile_property_sends_string_uuid_and_point_id():
    """async_get_charging_pile_property sends a string uuid + point_id and returns raw data (#93)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {"value": "32", "unit": "A"}})

    result = await auth.async_get_charging_pile_property(101, 21050)

    assert auth._post.call_args.args[0] == ua._CHARGING_PILE_PROPERTY_PATH
    body = auth._post.call_args.args[1]
    assert body["uuid"] == "101"
    assert body["point_id"] == "21050"
    assert result == {"value": "32", "unit": "A"}


# --- Battery capacity & SoC (#94) -------------------------------------------


async def test_get_battery_capacity_sends_ps_id():
    """async_get_battery_capacity posts ps_id to getBatteryCapacityByPsIdV2 (#94)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(
        return_value={"result_msg": "success", "result_data": {"capacity": {"value": "9.6", "unit": "kWh"}}}
    )

    result = await auth.async_get_battery_capacity("123")

    assert auth._post.call_args.args[0] == ua._BATTERY_CAPACITY_PATH
    assert auth._post.call_args.args[1]["ps_id"] == "123"
    assert result["capacity"] == {"value": "9.6", "unit": "kWh"}


async def test_get_battery_capacity_empty_when_no_data():
    """A response without result_data yields an empty dict."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success"})

    assert await auth.async_get_battery_capacity("123") == {}


async def test_get_battery_info_sends_only_ps_id_by_default():
    """async_get_battery_info sends ps_id alone when no optional params are given (#94)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {"soh": "98"}})

    result = await auth.async_get_battery_info("123")

    assert auth._post.call_args.args[0] == ua._BATTERY_INFO_PATH
    body = auth._post.call_args.args[1]
    assert body["ps_id"] == "123"
    assert "query_type" not in body
    assert "date_id" not in body
    assert "minute_interval" not in body
    assert result["soh"] == "98"


async def test_get_battery_info_forwards_optional_params():
    """query_type, date_id and minute_interval are forwarded (stringified) when supplied (#94)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {}})

    await auth.async_get_battery_info("123", query_type=1, date_id="20260718", minute_interval=5)

    body = auth._post.call_args.args[1]
    assert body["query_type"] == "1"
    assert body["date_id"] == "20260718"
    assert body["minute_interval"] == "5"


async def test_get_soc_by_ps_id_sends_ps_id():
    """async_get_soc_by_ps_id posts ps_id to querySocByPsId (#94)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {"soc": "73"}})

    result = await auth.async_get_soc_by_ps_id("123")

    assert auth._post.call_args.args[0] == ua._SOC_BY_PS_ID_PATH
    assert auth._post.call_args.args[1]["ps_id"] == "123"
    assert result["soc"] == "73"


async def test_get_soc_by_sn_sends_bt_sn():
    """async_get_soc_by_sn posts bt_sn to querySocBySn (#94)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {"soc": "51"}})

    result = await auth.async_get_soc_by_sn("BT-SN-001")

    assert auth._post.call_args.args[0] == ua._SOC_BY_SN_PATH
    assert auth._post.call_args.args[1]["bt_sn"] == "BT-SN-001"
    assert result["soc"] == "51"


async def test_get_soc_by_sn_empty_when_no_data():
    """A response without result_data yields an empty dict."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success"})

    assert await auth.async_get_soc_by_sn("BT-SN-001") == {}


# --- Fault / alarm reads (#96) ----------------------------------------------


async def test_get_fault_count_sends_ps_id():
    """async_get_fault_count posts ps_id to getDevFaultCountByPsId (#96)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {"fault_count": 3}})

    result = await auth.async_get_fault_count("123")

    assert auth._post.call_args.args[0] == ua._FAULT_COUNT_BY_PS_PATH
    assert auth._post.call_args.args[1]["ps_id"] == "123"
    assert result["fault_count"] == 3


async def test_get_fault_count_empty_when_no_data():
    """A response without result_data yields an empty dict."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success"})

    assert await auth.async_get_fault_count("123") == {}


async def test_get_fault_detail_sends_fault_code():
    """async_get_fault_detail posts fault_code to getFaultDetail (#96)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {"fault_name": "Overvoltage"}})

    result = await auth.async_get_fault_detail(101)

    assert auth._post.call_args.args[0] == ua._FAULT_DETAIL_PATH
    assert auth._post.call_args.args[1]["fault_code"] == "101"
    assert result["fault_name"] == "Overvoltage"


async def test_query_faults_returns_page_list_and_sends_defaults():
    """async_query_faults returns the pageList and sends curPage/size defaults (#96)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(
        return_value={"result_msg": "success", "result_data": {"pageList": [{"fault_code": "1"}, {"fault_code": "2"}]}}
    )

    faults = await auth.async_query_faults("123")

    assert auth._post.call_args.args[0] == ua._FAULT_LIST_PATH
    body = auth._post.call_args.args[1]
    assert body["ps_id"] == "123"
    assert body["curPage"] == 1
    assert body["size"] == 10
    assert isinstance(body["curPage"], int)
    assert [f["fault_code"] for f in faults] == ["1", "2"]


async def test_query_faults_forwards_optional_filters_with_camelcase_times():
    """Optional filters map onto the app's camelCase startTime/endTime params (#96)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {"pageList": []}})

    await auth.async_query_faults(
        ps_key="k1",
        uuid="dev-1",
        process_status=1,
        fault_type=2,
        fault_type_code=3,
        share_type="0,1,2",
        start_time="20260101000000",
        end_time="20260102000000",
        sort_column="fault_time",
        sort_type="desc",
        fault_name_like="grid",
        cur_page=2,
        size=25,
    )

    body = auth._post.call_args.args[1]
    assert body["ps_key"] == "k1"
    assert body["uuid"] == "dev-1"
    assert body["process_status"] == "1"
    assert body["fault_type"] == "2"
    assert body["fault_type_code"] == "3"
    assert body["share_type"] == "0,1,2"
    assert body["startTime"] == "20260101000000"
    assert body["endTime"] == "20260102000000"
    assert body["sort_column"] == "fault_time"
    assert body["sort_type"] == "desc"
    assert body["fault_name_like"] == "grid"
    assert body["curPage"] == 2
    assert body["size"] == 25
    # ps_id must not be sent when it isn't supplied.
    assert "ps_id" not in body


async def test_query_faults_accepts_bare_list_and_empty():
    """A bare list result is returned as-is; a missing result yields []."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": [{"fault_code": "9"}]})
    assert await auth.async_query_faults("1") == [{"fault_code": "9"}]

    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {}})
    assert await auth.async_query_faults("1") == []


async def test_get_open_fault_num_sends_constant_share_type():
    """async_get_open_fault_num sends the app's constant share_type=0,1,2 (#96)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {"open_num": 4}})

    result = await auth.async_get_open_fault_num()

    assert auth._post.call_args.args[0] == ua._PS_OPEN_FAULT_NUM_PATH
    assert auth._post.call_args.args[1]["share_type"] == "0,1,2"
    assert result["open_num"] == 4


async def test_get_unread_fault_count_forwards_supplied_params_only():
    """async_get_unread_fault_count sends only the supplied type/ps_id/uuid params (#96)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {"count": 2}})

    result = await auth.async_get_unread_fault_count(type="3", ps_id="123")

    assert auth._post.call_args.args[0] == ua._NOT_READ_FAULT_COUNT_PATH
    body = auth._post.call_args.args[1]
    assert body["type"] == "3"
    assert body["ps_id"] == "123"
    assert "uuid" not in body
    assert result["count"] == 2


async def test_get_unread_fault_count_forwards_uuid():
    """The uuid param is forwarded when supplied (#96)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {}})

    await auth.async_get_unread_fault_count(type="3", uuid="dev-9")

    body = auth._post.call_args.args[1]
    assert body["uuid"] == "dev-9"
    assert "ps_id" not in body


# --- Aggregate energy / report reads (#97) ----------------------------------


async def test_get_household_storage_report_sends_ps_id_and_version_tag():
    """async_get_household_storage_report sends ps_id + version_tag=1 by default (#97)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {"total": "12.3"}})

    result = await auth.async_get_household_storage_report("123")

    assert auth._post.call_args.args[0] == ua._HOUSEHOLD_STORAGE_REPORT_PATH
    body = auth._post.call_args.args[1]
    assert body["ps_id"] == "123"
    assert body["version_tag"] == "1"
    assert "date_type" not in body
    assert "date_id" not in body
    assert "minute_interval" not in body
    assert result["total"] == "12.3"


async def test_get_household_storage_report_forwards_optional_params():
    """date_type, date_id and minute_interval are forwarded (stringified) when supplied (#97)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {}})

    await auth.async_get_household_storage_report("123", date_type=2, date_id="202601", minute_interval=15)

    body = auth._post.call_args.args[1]
    assert body["date_type"] == "2"
    assert body["date_id"] == "202601"
    assert body["minute_interval"] == "15"


async def test_get_energy_summary_sends_ps_id_and_optional_params():
    """async_get_energy_summary posts ps_id and optional date params to getPsEnergySummaryInfo (#97)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {"today_energy": "9.1"}})

    result = await auth.async_get_energy_summary("123", date_type=1, date_id="20260718")

    assert auth._post.call_args.args[0] == ua._PS_ENERGY_SUMMARY_PATH
    body = auth._post.call_args.args[1]
    assert body["ps_id"] == "123"
    assert body["date_type"] == "1"
    assert body["date_id"] == "20260718"
    assert result["today_energy"] == "9.1"


async def test_get_energy_summary_empty_when_no_data():
    """A response without result_data yields an empty dict, and no date params are sent."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success"})

    assert await auth.async_get_energy_summary("123") == {}
    body = auth._post.call_args.args[1]
    assert "date_type" not in body
    assert "date_id" not in body


# --- Per-device day/month/year & minute history reads (#98) -----------------


async def test_get_device_day_month_year_history_sends_ps_key_only_by_default():
    """async_get_device_day_month_year_history sends ps_key alone by default (#98)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": [{"time": "20260101"}]})

    result = await auth.async_get_device_day_month_year_history("dev-key-1")

    assert auth._post.call_args.args[0] == ua._DEVICE_DMY_HISTORY_PATH
    body = auth._post.call_args.args[1]
    assert body["ps_key"] == "dev-key-1"
    assert "data_point" not in body
    assert "is_get_point_info" not in body
    # The raw result_data is returned verbatim (shape unverified).
    assert result == [{"time": "20260101"}]


async def test_get_device_day_month_year_history_forwards_all_params():
    """All optional params (incl. is_get_point_info=1) are forwarded when supplied (#98)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {}})

    await auth.async_get_device_day_month_year_history(
        "k",
        data_point="p1,p2",
        start_time="20260101",
        end_time="20261231",
        data_type=2,
        order=1,
        query_type=3,
        is_get_point_info=True,
    )

    body = auth._post.call_args.args[1]
    assert body["data_point"] == "p1,p2"
    assert body["start_time"] == "20260101"
    assert body["end_time"] == "20261231"
    assert body["data_type"] == "2"
    assert body["order"] == "1"
    assert body["query_type"] == "3"
    assert body["is_get_point_info"] == 1


async def test_get_device_minute_history_sends_verified_shape():
    """async_get_device_minute_history posts the verified ps_key/points/timestamp shape (#98)."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(
        return_value={"result_msg": "success", "result_data": [{"time_stamp": "20260718000000", "p1": "5"}]}
    )

    result = await auth.async_get_device_minute_history(
        "k",
        points=["p13003", "p13004"],
        start_time="20260718000000",
        end_time="20260718010000",
    )

    assert auth._post.call_args.args[0] == ua._DEVICE_MINUTE_HISTORY_PATH
    body = auth._post.call_args.args[1]
    assert body["ps_key"] == "k"
    assert body["points"] == "p13003,p13004"
    assert body["start_time_stamp"] == "20260718000000"
    assert body["end_time_stamp"] == "20260718010000"
    assert body["minute_interval"] == "5"
    assert result == [{"time_stamp": "20260718000000", "p1": "5"}]
