"""Unit tests for UserControl (user-token param Setting, #271)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from pysolarcloud import PySolarCloudException, Server, UserAuth
from pysolarcloud.user_control import UserControl


def _auth() -> UserAuth:
    return UserAuth(Server.Europe, "me@example.com", "secret", websession=MagicMock())


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
    auth.async_request.assert_awaited_once()
    args = auth.async_request.await_args
    assert args[0][0] == "/openapi/paramSettingCheck"
    assert args[0][1]["set_type"] == 0


async def test_read_parameters_submits_and_polls_to_success(monkeypatch):
    auth = _auth()
    submit = {
        "result_code": "1",
        "result_msg": "success",
        "result_data": {
            "check_result": "1",
            "dev_result_list": [{"code": "1", "task_id": "T1", "uuid": "9"}],
        },
    }
    running = {
        "result_code": "1",
        "result_msg": "success",
        "result_data": {"command_status": 2, "task_id": "T1", "param_list": []},
    }
    done = {
        "result_code": "1",
        "result_msg": "success",
        "result_data": {
            "command_status": 8,
            "task_id": "T1",
            "param_list": [
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
    auth.async_request = AsyncMock(side_effect=[submit, running, done])
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
