"""Tests for the Plants extensions added in the KRoperUK fork."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientResponse

from pysolarcloud.plants import DeviceType, Plants


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
def plants(auth):
    return Plants(auth)


@pytest.mark.asyncio
async def test_realtime_data_merges_extra_measure_points(auth, plants):
    """Extra measure points are requested without mutating the class dict."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_msg": "success",
            "result_data": {
                "point_dict": [
                    {"point_id": "99999", "point_name": "Battery Charge", "point_unit": "W"},
                ],
                "device_point_list": [
                    {"ps_id": "123", "p99999": "1500"},
                ],
            },
        }
    )

    data = await plants.async_get_realtime_data("123", extra_measure_points={"99999": "battery_charge_power"})

    assert data["123"]["battery_charge_power"]["value"] == 1500.0
    assert data["123"]["battery_charge_power"]["unit"] == "W"
    # Ensure class-level dict remains unchanged
    assert "99999" not in Plants.measure_points


@pytest.mark.asyncio
async def test_realtime_data_filter_by_measure_points(auth, plants):
    """Caller can request specific codes by name, including extra ones."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_msg": "success",
            "result_data": {
                "point_dict": [
                    {"point_id": "83033", "point_name": "Power", "point_unit": "W"},
                    {"point_id": "99998", "point_name": "Discharge", "point_unit": "W"},
                ],
                "device_point_list": [
                    {"ps_id": "123", "p83033": "3000", "p99998": "2000"},
                ],
            },
        }
    )

    data = await plants.async_get_realtime_data(
        "123",
        measure_points=["power", "battery_discharge_power"],
        extra_measure_points={"99998": "battery_discharge_power"},
    )

    assert set(data["123"].keys()) == {"power", "battery_discharge_power"}
    assert data["123"]["battery_discharge_power"]["value"] == 2000.0


@pytest.mark.asyncio
async def test_device_realtime_returns_data(auth, plants):
    """Device realtime endpoint returns per-device data when available."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_msg": "success",
            "result_data": {
                "point_dict": [
                    {"point_id": "11111", "point_name": "EV Power", "point_unit": "W"},
                ],
                "device_point_list": [
                    {"uuid": "dev-1", "p11111": "7000"},
                ],
            },
        }
    )

    data = await plants.async_get_device_realtime("123", DeviceType.METER)

    assert "dev-1" in data
    assert data["dev-1"]["11111"]["value"] == 7000.0


@pytest.mark.asyncio
async def test_device_realtime_gracefully_degrades_on_404(auth, plants):
    """Device realtime endpoint returns {} when the upstream endpoint is absent."""
    auth.request.return_value = _mock_response(
        {"result_code": "E996", "result_msg": "api not found", "result_data": None}, status=404
    )

    data = await plants.async_get_device_realtime("123", 7)

    assert data == {}


@pytest.mark.asyncio
async def test_device_realtime_swallows_known_api_errors(auth, plants):
    """Known soft errors from the device endpoint are treated as "unsupported"."""
    auth.request.return_value = _mock_response(
        {"result_code": "E996", "result_msg": "api not found", "result_data": None}
    )

    data = await plants.async_get_device_realtime("123", DeviceType.METER)

    assert data == {}


@pytest.mark.asyncio
async def test_device_realtime_raises_on_unexpected_error(auth, plants):
    """Unexpected errors still raise PySolarCloudException."""
    from pysolarcloud import PySolarCloudException

    auth.request.return_value = _mock_response(
        {"result_code": "E00003", "result_msg": "The token is invalid or has expired", "result_data": None}
    )

    with pytest.raises(PySolarCloudException):
        await plants.async_get_device_realtime("123", DeviceType.METER)


@pytest.mark.asyncio
async def test_device_realtime_accepts_int_device_type(auth, plants):
    """Numeric device type strings are accepted."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_msg": "success",
            "result_data": {
                "point_dict": [],
                "device_point_list": [],
            },
        }
    )

    await plants.async_get_device_realtime("123", "7")
    call_kwargs = auth.request.call_args.kwargs
    assert "device_type" in auth.request.call_args.args[1] or "data" in call_kwargs
    body = auth.request.call_args.args[1]
    assert body["device_type"] == "7"


@pytest.mark.asyncio
async def test_measure_points_dict_unchanged_after_extra_call(auth, plants):
    """The class-level measure_points map is never mutated by extra_measure_points."""
    original = dict(Plants.measure_points)
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_msg": "success",
            "result_data": {
                "point_dict": [],
                "device_point_list": [{"ps_id": "123"}],
            },
        }
    )

    await plants.async_get_realtime_data("123", extra_measure_points={"11111": "x"})

    assert Plants.measure_points == original


@pytest.mark.asyncio
async def test_realtime_data_error_raises(auth, plants):
    """Error responses (result_code != "1") still raise."""
    from pysolarcloud import PySolarCloudException

    auth.request.return_value = _mock_response(
        {"result_code": "E00003", "result_msg": "The token is invalid or has expired", "result_data": None}
    )

    with pytest.raises(PySolarCloudException):
        await plants.async_get_realtime_data("123")


@pytest.mark.asyncio
async def test_realtime_data_error_exposes_result_code_on_error_attr(auth, plants):
    """A result_code != "1" response raises with .error == the result_code (not a KeyError)."""
    from pysolarcloud import PySolarCloudException

    auth.request.return_value = _mock_response(
        {"result_code": "E00003", "result_msg": "The token is invalid or has expired", "result_data": None}
    )

    with pytest.raises(PySolarCloudException) as exc:
        await plants.async_get_realtime_data("123")
    assert exc.value.error == "E00003"
    assert exc.value.result_msg == "The token is invalid or has expired"


@pytest.mark.asyncio
async def test_device_realtime_uses_default_measure_points(auth, plants):
    """Device realtime uses the canonical measure_points map by default."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_msg": "success",
            "result_data": {
                "point_dict": [],
                "device_point_list": [],
            },
        }
    )

    await plants.async_get_device_realtime("123", DeviceType.METER)
    body = auth.request.call_args.args[1]
    assert body["point_id_list"] == list(Plants.measure_points.keys())


@pytest.mark.asyncio
async def test_device_realtime_merges_extra_points(auth, plants):
    """Device realtime can request additional point IDs."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_msg": "success",
            "result_data": {
                "point_dict": [],
                "device_point_list": [],
            },
        }
    )

    await plants.async_get_device_realtime("123", DeviceType.METER, extra_measure_points={"11111": "ev_power"})
    body = auth.request.call_args.args[1]
    assert "11111" in body["point_id_list"]
    assert "83033" in body["point_id_list"]


@pytest.mark.asyncio
async def test_historical_data_list_plant_id_produces_list_ps_id_list(auth, plants):
    """A list of plant IDs is passed through as a list ps_id_list, not its repr string."""
    from datetime import datetime

    auth.request.return_value = _mock_response(
        {"result_code": "1", "result_msg": "success", "result_data": {"point_dict": []}}
    )

    await plants.async_get_historical_data(["123", "456"], datetime(2024, 1, 1, 0, 0, 0))

    body = auth.request.call_args.args[1]
    assert body["ps_id_list"] == ["123", "456"]


@pytest.mark.asyncio
async def test_historical_data_scalar_plant_id_is_wrapped_in_list(auth, plants):
    """A scalar plant ID is wrapped in a single-element list ps_id_list."""
    from datetime import datetime

    auth.request.return_value = _mock_response(
        {"result_code": "1", "result_msg": "success", "result_data": {"point_dict": []}}
    )

    await plants.async_get_historical_data("123", datetime(2024, 1, 1, 0, 0, 0))

    body = auth.request.call_args.args[1]
    assert body["ps_id_list"] == ["123"]


@pytest.mark.asyncio
async def test_realtime_data_applies_raise_for_status(auth, plants):
    """Realtime data raises for HTTP status uniformly, regardless of the session flag."""
    resp = _mock_response(
        {
            "result_code": "1",
            "result_msg": "success",
            "result_data": {"point_dict": [], "device_point_list": []},
        }
    )
    auth.request.return_value = resp

    await plants.async_get_realtime_data("123")

    resp.raise_for_status.assert_called_once()


@pytest.mark.asyncio
async def test_historical_data_applies_raise_for_status(auth, plants):
    """Historical data raises for HTTP status uniformly, regardless of the session flag."""
    from datetime import datetime

    resp = _mock_response({"result_code": "1", "result_msg": "success", "result_data": {"point_dict": []}})
    auth.request.return_value = resp

    await plants.async_get_historical_data("123", datetime(2024, 1, 1, 0, 0, 0))

    resp.raise_for_status.assert_called_once()


@pytest.mark.asyncio
async def test_get_dev_property_point_value_posts_expected_params(auth, plants):
    """getDevPropertyPointValue hits the right URI with ps/device/point params and returns result_data."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_msg": "success",
            "result_data": {"18290": {"point_id": "18290", "value": "1060"}},
        }
    )

    data = await plants.async_get_dev_property_point_value("123", DeviceType.ENERGY_STORAGE_SYSTEM, ["18290", "18291"])

    uri = auth.request.call_args.args[0]
    body = auth.request.call_args.args[1]
    assert uri == "/openapi/platform/getDevPropertyPointValue"
    assert body["ps_id"] == "123"
    assert body["device_type"] == str(DeviceType.ENERGY_STORAGE_SYSTEM.value)
    assert body["point_id_list"] == ["18290", "18291"]
    assert data == {"18290": {"point_id": "18290", "value": "1060"}}


@pytest.mark.asyncio
async def test_get_dev_property_point_value_accepts_int_device_type(auth, plants):
    """Numeric / string device types are normalised to the numeric string form."""
    auth.request.return_value = _mock_response({"result_code": "1", "result_msg": "success", "result_data": {}})

    await plants.async_get_dev_property_point_value("123", "14", ["29046"])
    body = auth.request.call_args.args[1]
    assert body["device_type"] == "14"
    assert body["point_id_list"] == ["29046"]


@pytest.mark.asyncio
async def test_get_dev_property_point_value_raises_on_error(auth, plants):
    """A result_code != "1" raises PySolarCloudException."""
    from pysolarcloud import PySolarCloudException

    auth.request.return_value = _mock_response(
        {"result_code": "E00003", "result_msg": "The token is invalid or has expired", "result_data": None}
    )

    with pytest.raises(PySolarCloudException):
        await plants.async_get_dev_property_point_value("123", DeviceType.ENERGY_STORAGE_SYSTEM, ["18290"])


@pytest.mark.asyncio
async def test_get_dev_property_point_value_applies_raise_for_status(auth, plants):
    """The wrapper enforces HTTP status via raise_for_status."""
    resp = _mock_response({"result_code": "1", "result_msg": "success", "result_data": {}})
    auth.request.return_value = resp

    await plants.async_get_dev_property_point_value("123", DeviceType.ENERGY_STORAGE_SYSTEM, ["18290"])
    resp.raise_for_status.assert_called_once()


@pytest.mark.asyncio
async def test_get_open_point_info_posts_expected_params(auth, plants):
    """getOpenPointInfo hits the right URI, forwards the device type and returns result_data."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_msg": "success",
            "result_data": {"point_list": [{"point_id": "83022", "point_name": "Daily Yield"}]},
        }
    )

    data = await plants.async_get_open_point_info(device_type=DeviceType.INVERTER)

    uri = auth.request.call_args.args[0]
    body = auth.request.call_args.args[1]
    assert uri == "/openapi/platform/getOpenPointInfo"
    assert body["device_type"] == str(DeviceType.INVERTER.value)
    assert data == {"point_list": [{"point_id": "83022", "point_name": "Daily Yield"}]}


@pytest.mark.asyncio
async def test_get_open_point_info_omits_device_type_when_absent(auth, plants):
    """When no device type is supplied the request body omits it."""
    auth.request.return_value = _mock_response(
        {"result_code": "1", "result_msg": "success", "result_data": {"point_list": []}}
    )

    await plants.async_get_open_point_info()
    body = auth.request.call_args.args[1]
    assert "device_type" not in body


@pytest.mark.asyncio
async def test_get_open_point_info_raises_on_error(auth, plants):
    """A result_code != "1" raises PySolarCloudException."""
    from pysolarcloud import PySolarCloudException

    auth.request.return_value = _mock_response(
        {"result_code": "E00003", "result_msg": "The token is invalid or has expired", "result_data": None}
    )

    with pytest.raises(PySolarCloudException):
        await plants.async_get_open_point_info(device_type=DeviceType.INVERTER)


def test_measure_points_contains_load_shedding_loss():
    """Plant point 83743 (daily yield loss due to load shedding) is catalogued."""
    assert Plants.measure_points["83743"] == "daily_yield_loss_load_shedding"


def test_device_type_new_members_exist():
    """The DeviceType enum exposes the members documented in Appendix 1."""
    assert DeviceType.CHARGER.value == 51
    assert DeviceType.OPTIMIZER.value == 41
    assert DeviceType.MICROINVERTER.value == 55
    assert DeviceType.DIESEL_GENERATOR.value == 63
    # Value round-trips
    assert DeviceType(51).name == "CHARGER"
    assert DeviceType(51) is DeviceType.CHARGER


def test_device_type_37_alias_and_backwards_compat():
    """37 keeps its existing name while also being reachable as the documented PCS alias."""
    # The historical name must keep working.
    assert DeviceType.ENERGY_STORAGE_SYSTEM_2.value == 37
    assert DeviceType(37) is DeviceType.ENERGY_STORAGE_SYSTEM_2
    # PCS is an alias for the same member (per Appendix 1).
    assert DeviceType.PCS is DeviceType.ENERGY_STORAGE_SYSTEM_2


if __name__ == "__main__":
    pytest.main([__file__])
