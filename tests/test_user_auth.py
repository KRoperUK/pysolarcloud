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


async def test_get_plant_detail_returns_result_data():
    """async_get_plant_detail returns the plant's result_data payload (#269)."""
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

    # ps_id is forwarded in the request body, and the raw result_data comes back.
    assert auth._post.call_args.args[1]["ps_id"] == "5"
    assert detail["curr_power"] == {"value": "3200", "unit": "W"}


async def test_get_plant_detail_empty_when_no_data():
    """A response without result_data yields an empty dict, not an error."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success"})

    assert await auth.async_get_plant_detail(5) == {}


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


# --- async_get_device_realtime (#53) ----------------------------------------


async def test_get_device_realtime_returns_result_data():
    """async_get_device_realtime returns the per-device point data."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(
        return_value={
            "result_msg": "success",
            "result_data": {"13003": {"value": "240.5", "unit": "V"}, "13004": {"value": "1.2", "unit": "A"}},
        }
    )

    result = await auth.async_get_device_realtime("123", "SN123456")

    assert result["13003"] == {"value": "240.5", "unit": "V"}
    body = auth._post.call_args.args[1]
    assert body["sn"] == "SN123456"


async def test_get_device_realtime_with_point_ids():
    """When point_ids is specified, they are passed as a comma-separated points field."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    auth._post = AsyncMock(return_value={"result_msg": "success", "result_data": {}})

    await auth.async_get_device_realtime("123", "SN1", point_ids=["13003", "13004"])

    body = auth._post.call_args.args[1]
    assert body["points"] == "13003,13004"


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
