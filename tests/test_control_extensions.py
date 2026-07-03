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


@pytest.mark.asyncio
async def test_param_config_verification_true_when_supported(auth, control):
    """A "1" check_result at both levels reports the device supports the operation."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_data": {"check_result": "1", "dev_result_list": [{"check_result": "1"}]},
        }
    )

    assert await control.async_param_config_verification("dev-1", 0) is True
    body = auth.request.call_args.args[1]
    assert body == {"set_type": 0, "uuid": "dev-1"}


@pytest.mark.asyncio
async def test_param_config_verification_false_when_unsupported(auth, control):
    """A device-level check_result != "1" reports the operation is unsupported."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_data": {"check_result": "1", "dev_result_list": [{"check_result": "0"}]},
        }
    )
    assert await control.async_param_config_verification("dev-1", 0) is False


@pytest.mark.asyncio
async def test_param_config_verification_raises_on_error(auth, control):
    """A result_code != "1" raises rather than reporting unsupported."""
    auth.request.return_value = _mock_response({"result_code": "E00003", "result_data": None})
    with pytest.raises(PySolarCloudException):
        await control.async_param_config_verification("dev-1", 0)


@pytest.mark.asyncio
async def test_check_read_and_update_support_use_correct_set_type(auth, control):
    """Read support probes set_type 2; update support probes set_type 0."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_data": {"check_result": "1", "dev_result_list": [{"check_result": "1"}]},
        }
    )
    assert await control.async_check_read_support("dev-1") is True
    assert auth.request.call_args.args[1]["set_type"] == 2
    assert await control.async_check_update_support("dev-1") is True
    assert auth.request.call_args.args[1]["set_type"] == 0


@pytest.mark.asyncio
async def test_wait_for_task_returns_param_list_on_success(auth, control):
    """command_status 8 returns the param_list from result_data."""
    auth.request.return_value = _mock_response(
        {"result_code": "1", "result_data": {"command_status": 8, "param_list": [{"param_code": "10001"}]}}
    )
    with patch("pysolarcloud.control.asyncio.sleep", new=AsyncMock()):
        result = await control.wait_for_task("dev-1", "t-1")
    assert result == [{"param_code": "10001"}]


@pytest.mark.asyncio
async def test_wait_for_task_raises_on_failed_status(auth, control):
    """A non-running, non-success command_status raises PySolarCloudException."""
    auth.request.return_value = _mock_response({"result_code": "1", "result_data": {"command_status": 5}})
    with (
        patch("pysolarcloud.control.asyncio.sleep", new=AsyncMock()),
        pytest.raises(PySolarCloudException),
    ):
        await control.wait_for_task("dev-1", "t-1")


@pytest.mark.asyncio
async def test_read_parameters_returns_formatted_readouts(auth, control):
    """A successful read submits a set_type 2 task, waits, and formats each returned param."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_data": {"check_result": "1", "dev_result_list": [{"code": "1", "task_id": "t-1"}]},
        }
    )
    readout = [{"param_code": "10001", "point_name": "SOC Upper", "return_value": "900", "unit": "%"}]
    with patch.object(control, "wait_for_task", new=AsyncMock(return_value=readout)):
        result = await control.async_read_parameters("dev-1", ["soc_upper_limit"])

    body = auth.request.call_args.args[1]
    assert body["set_type"] == 2
    assert body["param_list"] == [{"param_code": "10001", "set_value": ""}]
    assert result[0]["id"] == "10001"
    assert result[0]["code"] == "soc_upper_limit"
    assert result[0]["value"] == 900.0


@pytest.mark.asyncio
async def test_read_parameters_defaults_to_all_config_parameters(auth, control):
    """With no param_list, every known config-parameter code is requested."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_data": {"check_result": "1", "dev_result_list": [{"code": "1", "task_id": "t-1"}]},
        }
    )
    with patch.object(control, "wait_for_task", new=AsyncMock(return_value=[])):
        await control.async_read_parameters("dev-1")

    body = auth.request.call_args.args[1]
    sent_codes = {p["param_code"] for p in body["param_list"]}
    assert sent_codes == set(Control.config_parameters.keys())


@pytest.mark.asyncio
async def test_wait_for_task_polls_until_complete(auth, control):
    """A running task (command_status 2) is polled again until it completes."""
    running = _mock_response({"result_code": "1", "result_data": {"command_status": 2}})
    done = _mock_response(
        {"result_code": "1", "result_data": {"command_status": 8, "param_list": [{"param_code": "10005"}]}}
    )
    auth.request.side_effect = [running, done]
    with patch("pysolarcloud.control.asyncio.sleep", new=AsyncMock()):
        result = await control.wait_for_task("dev-1", "t-1", timeout=100)

    assert result == [{"param_code": "10005"}]
    assert auth.request.call_count == 2


@pytest.mark.asyncio
async def test_read_parameters_raises_when_not_accepted(auth, control):
    """A rejected read task (check_result != "1") raises PySolarCloudException."""
    auth.request.return_value = _mock_response(
        {"result_code": "1", "result_data": {"check_result": "0", "dev_result_list": []}}
    )
    with pytest.raises(PySolarCloudException):
        await control.async_read_parameters("dev-1", ["soc_upper_limit"])


@pytest.mark.asyncio
async def test_update_parameters_raises_when_rejected(auth, control):
    """A rejected update task (dev_result_list code != "1") raises PySolarCloudException."""
    auth.request.return_value = _mock_response(
        {"result_code": "1", "result_data": {"check_result": "1", "dev_result_list": [{"code": "0"}]}}
    )
    with pytest.raises(PySolarCloudException):
        await control.async_update_parameters("dev-1", {"soc_upper_limit": "900"})


def test_format_param_readout_maps_enum_value_sets(control):
    """A param with set_val_name resolves the raw value to its display name + value_set."""
    param = {
        "param_code": "10004",
        "point_name": "Charge/Discharge Command",
        "unit": "",
        "set_val_name": "Charge|Discharge|Stop",
        "set_val_name_val": "170|187|204",
    }
    readout = control._format_param_readout(param, "187")
    assert readout["value"] == "Discharge"
    assert readout["value_set"] == {"Charge": "170", "Discharge": "187", "Stop": "204"}
    assert readout["code"] == "charge_discharge_command"


def test_format_param_readout_coerces_plain_numeric_value(control):
    """Without a value-set, a numeric readout value is coerced to float."""
    param = {"param_code": "10001", "point_name": "SOC Upper", "unit": "%"}
    readout = control._format_param_readout(param, "900")
    assert readout["value"] == 900.0
    assert readout["unit"] == "%"


@pytest.mark.asyncio
async def test_heartbeat_loop_rejects_invalid_interval(auth, control):
    """heartbeat_loop validates the interval before entering the loop."""
    stop = asyncio.Event()
    with pytest.raises(ValueError):
        await control.heartbeat_loop("dev-1", 0, stop)


@pytest.mark.asyncio
async def test_heartbeat_loop_continues_after_timeout(auth, control):
    """When the stop wait times out, the loop sends another heartbeat (continue branch)."""
    stop = asyncio.Event()
    calls = 0

    async def fake_heartbeat(uuid, interval):
        nonlocal calls
        calls += 1
        if calls >= 2:
            stop.set()

    async def fake_wait_for(coro, *args, **kwargs):
        coro.close()  # avoid "coroutine was never awaited" for stop_event.wait()
        raise TimeoutError

    with (
        patch.object(control, "async_heartbeat", new=AsyncMock(side_effect=fake_heartbeat)),
        patch("pysolarcloud.control.asyncio.wait_for", new=fake_wait_for),
    ):
        await control.heartbeat_loop("dev-1", 1, stop)

    assert calls >= 2


if __name__ == "__main__":
    pytest.main([__file__])
