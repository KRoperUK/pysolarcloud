"""Tests for the Plants extensions added in the KRoperUK fork."""

from unittest.mock import AsyncMock, MagicMock
import pytest
from aiohttp import ClientResponse
from pysolarcloud.plants import Plants, DeviceType


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
    auth.request.return_value = _mock_response({
        "result_data": {
            "point_dict": [
                {"point_id": "99999", "point_name": "Battery Charge", "point_unit": "W"},
            ],
            "device_point_list": [
                {"ps_id": "123", "p99999": "1500"},
            ],
        }
    })

    data = await plants.async_get_realtime_data("123", extra_measure_points={"99999": "battery_charge_power"})

    assert data["123"]["battery_charge_power"]["value"] == 1500.0
    assert data["123"]["battery_charge_power"]["unit"] == "W"
    # Ensure class-level dict remains unchanged
    assert "99999" not in Plants.measure_points


@pytest.mark.asyncio
async def test_realtime_data_filter_by_measure_points(auth, plants):
    """Caller can request specific codes by name, including extra ones."""
    auth.request.return_value = _mock_response({
        "result_data": {
            "point_dict": [
                {"point_id": "83033", "point_name": "Power", "point_unit": "W"},
                {"point_id": "99998", "point_name": "Discharge", "point_unit": "W"},
            ],
            "device_point_list": [
                {"ps_id": "123", "p83033": "3000", "p99998": "2000"},
            ],
        }
    })

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
    auth.request.return_value = _mock_response({
        "result_data": {
            "point_dict": [
                {"point_id": "11111", "point_name": "EV Power", "point_unit": "W"},
            ],
            "device_point_list": [
                {"uuid": "dev-1", "p11111": "7000"},
            ],
        }
    })

    data = await plants.async_get_device_realtime("123", DeviceType.METER)

    assert "dev-1" in data
    assert data["dev-1"]["11111"]["value"] == 7000.0


@pytest.mark.asyncio
async def test_device_realtime_gracefully_degrades_on_404(auth, plants):
    """Device realtime endpoint returns {} when the upstream endpoint is absent."""
    auth.request.return_value = _mock_response({"error": {"error": "not found"}}, status=404)

    data = await plants.async_get_device_realtime("123", 7)

    assert data == {}


@pytest.mark.asyncio
async def test_device_realtime_swallows_known_api_errors(auth, plants):
    """Known soft errors from the device endpoint are treated as "unsupported"."""
    auth.request.return_value = _mock_response({
        "error": {"error": "endpoint_not_found", "error_description": "Unknown endpoint"}
    })

    data = await plants.async_get_device_realtime("123", DeviceType.METER)

    assert data == {}


@pytest.mark.asyncio
async def test_device_realtime_raises_on_unexpected_error(auth, plants):
    """Unexpected errors still raise PySolarCloudException."""
    from pysolarcloud import PySolarCloudException
    auth.request.return_value = _mock_response({
        "error": {"error": "internal_error", "error_description": "boom"}
    })

    with pytest.raises(PySolarCloudException):
        await plants.async_get_device_realtime("123", DeviceType.METER)


@pytest.mark.asyncio
async def test_device_realtime_accepts_int_device_type(auth, plants):
    """Numeric device type strings are accepted."""
    auth.request.return_value = _mock_response({
        "result_data": {
            "point_dict": [],
            "device_point_list": [],
        }
    })

    await plants.async_get_device_realtime("123", "7")
    call_kwargs = auth.request.call_args.kwargs
    assert "device_type" in auth.request.call_args.args[1] or "data" in call_kwargs
    body = auth.request.call_args.args[1]
    assert body["device_type"] == "7"


@pytest.mark.asyncio
async def test_measure_points_dict_unchanged_after_extra_call(auth, plants):
    """The class-level measure_points map is never mutated by extra_measure_points."""
    original = dict(Plants.measure_points)
    auth.request.return_value = _mock_response({
        "result_data": {
            "point_dict": [],
            "device_point_list": [{"ps_id": "123"}],
        }
    })

    await plants.async_get_realtime_data("123", extra_measure_points={"11111": "x"})

    assert Plants.measure_points == original


@pytest.mark.asyncio
async def test_realtime_data_error_raises(auth, plants):
    """Error responses still raise."""
    from pysolarcloud import PySolarCloudException
    auth.request.return_value = _mock_response({"error": {"error": "auth"}})

    with pytest.raises(PySolarCloudException):
        await plants.async_get_realtime_data("123")


@pytest.mark.asyncio
async def test_device_realtime_uses_default_measure_points(auth, plants):
    """Device realtime uses the canonical measure_points map by default."""
    auth.request.return_value = _mock_response({
        "result_data": {
            "point_dict": [],
            "device_point_list": [],
        }
    })

    await plants.async_get_device_realtime("123", DeviceType.METER)
    body = auth.request.call_args.args[1]
    assert body["point_id_list"] == list(Plants.measure_points.keys())


@pytest.mark.asyncio
async def test_device_realtime_merges_extra_points(auth, plants):
    """Device realtime can request additional point IDs."""
    auth.request.return_value = _mock_response({
        "result_data": {
            "point_dict": [],
            "device_point_list": [],
        }
    })

    await plants.async_get_device_realtime(
        "123", DeviceType.METER, extra_measure_points={"11111": "ev_power"}
    )
    body = auth.request.call_args.args[1]
    assert "11111" in body["point_id_list"]
    assert "83033" in body["point_id_list"]


if __name__ == "__main__":
    pytest.main([__file__])
