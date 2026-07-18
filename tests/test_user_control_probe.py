"""Unit tests for the user-account control probe (#271 spike)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from pysolarcloud import PySolarCloudException, Server, UserAuth
from pysolarcloud import user_control_probe as probe
from pysolarcloud.user_control_probe import ProbeResult


def _auth() -> UserAuth:
    return UserAuth(Server.Europe, "me@example.com", "secret", websession=MagicMock())


async def test_request_soft_returns_failure_envelope_without_raising():
    """async_request_soft returns non-success envelopes; async_request still raises."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "42"
    fail = {"result_code": "E999", "result_msg": "er_unknown", "result_data": {}}
    auth._post = AsyncMock(return_value=fail)

    soft = await auth.async_request_soft("/openapi/paramSetting", {"uuid": "1"})
    assert soft == fail

    with pytest.raises(PySolarCloudException):
        await auth.async_request("/openapi/paramSetting", {"uuid": "1"})


async def test_request_soft_relogins_on_invalid_token_codes():
    """Token-invalid soft responses trigger one re-login then retry."""
    auth = _auth()
    auth.token = "old"
    auth.user_id = "42"
    invalid = {"result_code": "E00003", "result_msg": "er_token_login_invalid"}
    ok = {"result_code": "1", "result_msg": "success", "result_data": {"check_result": "1"}}
    auth._post = AsyncMock(
        side_effect=[invalid, {"result_msg": "success", "result_data": {"token": "new", "user_id": "42"}}, ok]
    )

    data = await auth.async_request_soft("/openapi/paramSettingCheck", {"uuid": "9", "set_type": 2})
    assert data["result_msg"] == "success"
    assert auth.token == "new"
    assert auth._post.await_count == 3


def test_classify_probe_results():
    """Classification matches the spike outcome table."""
    assert probe.classify_probe_results([]) == "inconclusive"
    unsupported = [
        ProbeResult("a", "/x", "E1", "fail", False, "code=E1"),
        ProbeResult("b", "/y", "E2", "fail", False, "code=E2"),
    ]
    assert probe.classify_probe_results(unsupported) == "unsupported"

    partial = [ProbeResult("c", "/v1/devService/paramSettingCheck", "1", "success", True, "ok")]
    assert probe.classify_probe_results(partial) == "partial"

    supported = [
        ProbeResult("d", probe.WORKING_SETTING_PATH, "1", "success", True, "ok"),
    ]
    assert probe.classify_probe_results(supported) == "supported_read"

    empty = [ProbeResult("e", "/z", None, None, False, "exception=TimeoutError")]
    assert probe.classify_probe_results(empty) == "inconclusive"


def test_select_dispatch_device_uuid_prefers_ess():
    """ESS (type 14) wins over inverter (type 1)."""
    devices = [
        {"uuid": "inv", "device_type": 1},
        {"uuid": "ess", "device_type": 14},
    ]
    assert probe.select_dispatch_device_uuid(devices) == "ess"
    assert probe.select_dispatch_device_uuid([{"uuid": "only", "device_type": 99}]) == "only"
    assert probe.select_dispatch_device_uuid([]) is None


async def test_probe_read_candidates_records_each_path():
    """Every candidate is hit; working /openapi/paramSetting is logical ok."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "1"

    async def soft(path: str, body=None):
        if "/platform/" in path:
            raise RuntimeError("401 Unauthorized")
        if path.endswith("paramSettingCheck"):
            return {
                "result_code": "1",
                "result_msg": "success",
                "result_data": {
                    "check_result": "1",
                    "dev_result_list": [{"check_result": "1", "uuid": "1"}],
                },
            }
        if path == probe.WORKING_SETTING_PATH:
            return {
                "result_code": "1",
                "result_msg": "success",
                "result_data": {
                    "check_result": "1",
                    "dev_result_list": [{"task_id": "9", "code": "1"}],
                },
            }
        if path.endswith("paramSetting"):
            return {"result_code": "1", "result_msg": "success", "result_data": {"code": "4"}}
        return {"result_code": "E404", "result_msg": "er_unknown_url"}

    auth.async_request_soft = soft  # type: ignore[method-assign]

    results = await probe.probe_read_candidates(auth, "device-uuid-1")
    assert len(results) == len(probe.READ_CANDIDATES)
    assert any(r.ok and r.path == probe.WORKING_SETTING_PATH for r in results)
    assert probe.classify_probe_results(results) == "supported_read"
    assert probe.pick_write_path(results) == ("openapi_paramSetting_write", probe.WORKING_SETTING_PATH)


async def test_probe_treats_paramsetting_code_4_as_logical_failure():
    """Envelope success + result_data.code 4 is not a working param task."""
    auth = _auth()
    auth.token = "T"
    auth.user_id = "1"

    async def soft(path: str, body=None):
        if path.endswith("paramSettingCheck"):
            return {
                "result_code": "1",
                "result_msg": "success",
                "result_data": {"check_result": "1", "check_msg": "success"},
            }
        if path.endswith("paramSetting"):
            return {"result_code": "1", "result_msg": "success", "result_data": {"code": "4"}}
        return {"result_code": "E404", "result_msg": "er_unknown_url"}

    auth.async_request_soft = soft  # type: ignore[method-assign]

    results = await probe.probe_read_candidates(auth, "device-uuid-1")
    assert any(r.ok and r.path.endswith("paramSettingCheck") for r in results)
    assert not any(r.ok and r.path.rstrip("/").endswith("/paramSetting") for r in results)
    assert probe.classify_probe_results(results) == "partial"
    assert probe.pick_write_path(results) is None


def test_write_ok_enabled(monkeypatch):
    """Write gate defaults off; accepts 1/true/yes."""
    monkeypatch.delenv(probe.WRITE_OK_ENV, raising=False)
    assert probe.write_ok_enabled() is False
    monkeypatch.setenv(probe.WRITE_OK_ENV, "1")
    assert probe.write_ok_enabled() is True
