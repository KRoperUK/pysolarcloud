"""Unit tests for UserControl (user-token param Setting, #271)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from pysolarcloud import PySolarCloudException, Server, UserAuth
from pysolarcloud.user_control import UserControl


def _auth() -> UserAuth:
    return UserAuth(Server.Europe, "me@example.com", "secret", websession=MagicMock())


def _ok_submit(task_id: str = "T1") -> dict:
    return {
        "result_code": "1",
        "result_msg": "success",
        "result_data": {
            "check_result": "1",
            "dev_result_list": [{"code": "1", "task_id": task_id, "uuid": "9"}],
        },
    }


def _ok_task(status: int = 8, params: list | None = None) -> dict:
    return {
        "result_code": "1",
        "result_msg": "success",
        "result_data": {
            "command_status": status,
            "task_id": "T1",
            "param_list": params
            if params is not None
            else [
                {
                    "param_code": "10011",
                    "point_name": "Startup/shutdown",
                    "return_value": "207",
                    "set_value": "",
                    "unit": "",
                }
            ],
        },
    }


async def test_check_update_support_true():
    auth = _auth()
    auth.async_request = AsyncMock(
        return_value={
            "result_code": "1",
            "result_msg": "success",
            "result_data": {
                "check_result": "1",
                "dev_result_list": [{"check_result": "1", "uuid": "9"}],
            },
        }
    )
    control = UserControl(auth)
    assert await control.async_check_update_support("9") is True
    args = auth.async_request.await_args
    assert args[0][0] == "/openapi/paramSettingCheck"
    assert args[0][1]["set_type"] == 0


async def test_check_update_support_false_and_top_level_only():
    auth = _auth()
    auth.async_request = AsyncMock(
        side_effect=[
            {"result_data": {"check_result": "0"}},
            {"result_data": {"check_result": "1"}},  # no dev list → True
            {
                "result_data": {
                    "check_result": "1",
                    "dev_result_list": [{"check_result": "0"}],
                }
            },
        ]
    )
    control = UserControl(auth)
    assert await control.async_check_update_support("9") is False
    assert await control.async_check_update_support("9") is True
    assert await control.async_check_update_support("9") is False


async def test_check_read_support():
    auth = _auth()
    auth.async_request = AsyncMock(
        side_effect=[
            {"result_data": {"check_result": "1", "dev_result_list": [{"check_result": "1"}]}},
            {"result_data": {"check_result": "1"}},
            {"result_data": {"check_result": "0"}},
        ]
    )
    control = UserControl(auth)
    assert await control.async_check_read_support("9") is True
    assert await control.async_check_read_support("9") is True
    assert await control.async_check_read_support("9") is False
    assert auth.async_request.await_args_list[0].args[1]["set_type"] == 2


async def test_read_parameters_submits_and_polls_to_success(monkeypatch):
    auth = _auth()
    auth.async_request = AsyncMock(side_effect=[_ok_submit(), _ok_task(2, []), _ok_task(8)])
    monkeypatch.setattr("pysolarcloud.user_control.asyncio.sleep", AsyncMock())
    control = UserControl(auth)
    rows = await control.async_read_parameters("9", ["power_on"])
    assert len(rows) == 1
    assert rows[0]["id"] == "10011"
    assert rows[0]["value"] in (207, 207.0, "207")
    paths = [c.args[0] for c in auth.async_request.await_args_list]
    assert paths[0] == "/openapi/paramSetting"
    assert paths[1] == "/openapi/getParamSettingTask"
    assert paths[2] == "/openapi/getParamSettingTask"


async def test_read_parameters_default_param_list_and_set_value_fallback(monkeypatch):
    auth = _auth()
    done = _ok_task(
        8,
        [
            {
                "param_code": "10011",
                "point_name": "Startup/shutdown",
                "return_value": "",
                "set_value": "207",
                "unit": "",
            }
        ],
    )
    auth.async_request = AsyncMock(side_effect=[_ok_submit(), done])
    monkeypatch.setattr("pysolarcloud.user_control.asyncio.sleep", AsyncMock())
    control = UserControl(auth)
    # None → all config_parameters codes (large list); just assert it submits.
    rows = await control.async_read_parameters("9", ["10011"])
    assert rows[0]["value"] in (207, 207.0, "207")


async def test_update_and_set_parameter(monkeypatch):
    auth = _auth()
    done = _ok_task(
        8,
        [
            {
                "param_code": "10012",
                "point_name": "Feed-in power limitation",
                "return_value": "85",
                "set_value": "85",
                "set_val_name": "Enable|Disable",
                "set_val_name_val": "170|85",
                "unit": "",
            }
        ],
    )
    auth.async_request = AsyncMock(side_effect=[_ok_submit(), done, _ok_submit(), done])
    monkeypatch.setattr("pysolarcloud.user_control.asyncio.sleep", AsyncMock())
    control = UserControl(auth)
    rows = await control.async_update_parameters("9", {"10012": "85"})
    assert rows
    rows2 = await control.async_set_parameter("9", "feed_in_limitation", "disable")
    assert rows2
    # set_type 0 on write
    assert auth.async_request.await_args_list[0].args[1]["set_type"] == 0


async def test_read_parameters_rejects_code_6():
    auth = _auth()
    auth.async_request = AsyncMock(
        return_value={
            "result_code": "1",
            "result_msg": "success",
            "result_data": {
                "check_result": "1",
                "dev_result_list": [{"code": "6", "uuid": "9"}],
            },
        }
    )
    control = UserControl(auth)
    with pytest.raises(PySolarCloudException, match="not accepted"):
        await control.async_read_parameters("9", ["10003"])


async def test_run_task_rejects_check_result_and_empty_dev_list(monkeypatch):
    auth = _auth()
    auth.async_request = AsyncMock(
        side_effect=[
            {"result_data": {"check_result": "9", "dev_result_list": []}},
            {"result_data": {"check_result": "1", "dev_result_list": []}},
        ]
    )
    monkeypatch.setattr("pysolarcloud.user_control.asyncio.sleep", AsyncMock())
    control = UserControl(auth)
    with pytest.raises(PySolarCloudException, match="check_result"):
        await control.async_read_parameters("9", ["10011"])
    with pytest.raises(PySolarCloudException, match="missing dev_result_list"):
        await control.async_read_parameters("9", ["10011"])


async def test_wait_for_task_failure_and_bad_status(monkeypatch):
    auth = _auth()
    fail = {
        "result_code": "1",
        "result_msg": "success",
        "result_data": {"command_status": 3, "task_id": "T1"},
    }
    auth.async_request = AsyncMock(side_effect=[_ok_submit(), fail])
    monkeypatch.setattr("pysolarcloud.user_control.asyncio.sleep", AsyncMock())
    control = UserControl(auth)
    with pytest.raises(PySolarCloudException, match="not successful"):
        await control.async_read_parameters("9", ["10011"])


async def test_wait_for_task_timeout(monkeypatch):
    auth = _auth()
    running = _ok_task(2, [])
    auth.async_request = AsyncMock(side_effect=[_ok_submit(), running, running])
    # First sleep (initial 2s), then poll sleep; force deadline in the past after first poll.
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    times = iter([100.0, 100.0, 9999.0])  # start, first poll check, deadline exceeded
    monkeypatch.setattr("pysolarcloud.user_control.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "pysolarcloud.user_control.asyncio.get_running_loop",
        lambda: type("L", (), {"time": lambda self: next(times)})(),
    )
    control = UserControl(auth)
    with pytest.raises(PySolarCloudException, match="Timed out"):
        await control.async_read_parameters("9", ["10011"])


async def test_wait_for_task_non_list_param_list(monkeypatch):
    auth = _auth()
    done = {
        "result_code": "1",
        "result_msg": "success",
        "result_data": {"command_status": 8, "param_list": "not-a-list"},
    }
    auth.async_request = AsyncMock(side_effect=[_ok_submit(), done])
    monkeypatch.setattr("pysolarcloud.user_control.asyncio.sleep", AsyncMock())
    control = UserControl(auth)
    rows = await control.async_read_parameters("9", ["10011"])
    assert rows == []


async def test_read_parameters_none_uses_all_config_codes(monkeypatch):
    """param_list=None expands to Control.config_parameters keys."""
    auth = _auth()
    auth.async_request = AsyncMock(side_effect=[_ok_submit(), _ok_task(8)])
    monkeypatch.setattr("pysolarcloud.user_control.asyncio.sleep", AsyncMock())
    control = UserControl(auth)
    await control.async_read_parameters("9", None)
    body = auth.async_request.await_args_list[0].args[1]
    assert len(body["param_list"]) > 10


async def test_wait_for_task_invalid_command_status(monkeypatch):
    auth = _auth()
    bad = {
        "result_code": "1",
        "result_msg": "success",
        "result_data": {"command_status": "nope", "task_id": "T1"},
    }
    auth.async_request = AsyncMock(side_effect=[_ok_submit(), bad])
    monkeypatch.setattr("pysolarcloud.user_control.asyncio.sleep", AsyncMock())
    control = UserControl(auth)
    with pytest.raises(PySolarCloudException, match="not successful"):
        await control.async_read_parameters("9", ["10011"])
