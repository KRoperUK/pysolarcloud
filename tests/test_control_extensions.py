"""Tests for the Control extensions added in the KRoperUK fork."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientResponse

from pysolarcloud import PySolarCloudException
from pysolarcloud.control import Control


def _mock_response(json_data: dict, *, status: int = 200) -> ClientResponse:
    response = MagicMock(spec=ClientResponse)
    response.status = status
    response.raise_for_status = MagicMock()
    response.json = AsyncMock(return_value=json_data)
    return response


@pytest.fixture
def auth():
    auth = MagicMock()
    auth.lang = "_en_US"
    auth.request = AsyncMock()
    return auth


@pytest.fixture
def control(auth):
    return Control(auth)


@pytest.mark.asyncio
async def test_async_heartbeat_sends_interval(auth, control):
    """async_heartbeat writes param 10017 with the supplied interval."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_data": {
                "check_result": "1",
                "dev_result_list": [{"code": "1", "task_id": "t-1"}],
            },
        }
    )
    with patch.object(control, "wait_for_task", new=AsyncMock(return_value=[])):
        await control.async_heartbeat("dev-1", 120)

    body = auth.request.call_args.args[1]
    assert body["param_list"][0]["param_code"] == "10017"
    assert body["param_list"][0]["set_value"] == "120"


@pytest.mark.asyncio
async def test_async_heartbeat_rejects_invalid_interval(auth, control):
    """async_heartbeat validates the interval range."""
    with pytest.raises(ValueError):
        await control.async_heartbeat("dev-1", 0)
    with pytest.raises(ValueError):
        await control.async_heartbeat("dev-1", 1001)


@pytest.mark.asyncio
async def test_heartbeat_loop_sends_then_stops(auth, control):
    """heartbeat_loop keeps sending until the stop event is set."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_data": {
                "check_result": "1",
                "dev_result_list": [{"code": "1", "task_id": "t-1"}],
            },
        }
    )
    with patch.object(control, "wait_for_task", new=AsyncMock(return_value=[])):
        stop = asyncio.Event()
        task = asyncio.create_task(control.heartbeat_loop("dev-1", 1, stop))
        await asyncio.sleep(0.05)
        stop.set()
        await task

    assert auth.request.call_count >= 1


@pytest.mark.asyncio
async def test_heartbeat_loop_survives_api_errors(auth, control):
    """A failed heartbeat does not kill the loop."""
    auth.request.side_effect = PySolarCloudException({"error": "timeout"})

    stop = asyncio.Event()
    task = asyncio.create_task(control.heartbeat_loop("dev-1", 1, stop))
    await asyncio.sleep(0.05)
    stop.set()
    await task

    assert auth.request.call_count >= 1


@pytest.mark.asyncio
async def test_wait_for_task_times_out_when_stuck_running(auth, control):
    """A task stuck in the running state raises instead of looping forever (#12)."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_data": {"command_status": 2},  # always "running"
        }
    )
    with (
        patch("pysolarcloud.control.asyncio.sleep", new=AsyncMock()),
        pytest.raises(PySolarCloudException, match="[Tt]imed out"),
    ):
        await control.wait_for_task("dev-1", "t-1", timeout=0)


def test_charge_discharge_command_value_mapping():
    """The canonical command names map to the expected on-the-wire values."""
    assert Control.CHARGE_DISCHARGE_COMMANDS == {
        "stop": "204",
        "charge": "170",
        "discharge": "187",
    }


def test_forced_charging_value_mapping():
    """The canonical forced-charging names map to the expected values."""
    assert Control.FORCED_CHARGING == {"disable": "85", "enable": "170"}


@pytest.mark.asyncio
async def test_control_forwards_non_default_lang_to_request(auth):
    """A non-default lang given to Control is forwarded to auth.request (#15)."""
    control = Control(auth, lang="_de_DE")
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_data": {
                "check_result": "1",
                "dev_result_list": [{"code": "1", "task_id": "t-1"}],
            },
        }
    )
    with patch.object(control, "wait_for_task", new=AsyncMock(return_value=[])):
        await control.async_update_parameters("dev-1", {"charge_discharge_power": "2500"})

    assert auth.request.call_args.kwargs["lang"] == "_de_DE"


@pytest.mark.asyncio
async def test_async_update_parameters_uses_value_map(auth, control):
    """async_update_parameters accepts canonical names from the Control helpers."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_data": {
                "check_result": "1",
                "dev_result_list": [{"code": "1", "task_id": "t-1"}],
            },
        }
    )
    with patch.object(control, "wait_for_task", new=AsyncMock(return_value=[])):
        await control.async_update_parameters(
            "dev-1",
            {
                "charge_discharge_command": Control.CHARGE_DISCHARGE_COMMANDS["charge"],
                "charge_discharge_power": "2500",
            },
        )

    body = auth.request.call_args.args[1]
    param_codes = {p["param_code"]: p["set_value"] for p in body["param_list"]}
    assert param_codes["10004"] == "170"
    assert param_codes["10005"] == "2500"


if __name__ == "__main__":
    pytest.main([__file__])
