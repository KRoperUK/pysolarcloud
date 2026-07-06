"""Tests for the Plants extensions added in the KRoperUK fork."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientResponse

from pysolarcloud.plants import DeviceFaultStaus, DeviceType, Plants


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

    data = await plants.async_get_device_realtime("123", DeviceType.METER, ps_key_list=["dev-1"])

    assert "dev-1" in data
    assert data["dev-1"]["11111"]["value"] == 7000.0


@pytest.mark.asyncio
async def test_device_realtime_unwraps_nested_device_point(auth, plants):
    """getDeviceRealTimeData nests the device fields under a "device_point" key.

    The uuid and p<id> values must be read from there; reading them at the top level
    yields uuid=None so every device is skipped and the result is empty (the bug that
    stopped all per-device sensors from being created).
    """
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_msg": "success",
            "result_data": {
                "point_dict": [
                    {"point_id": "96", "point_name": "String 1 Voltage", "point_unit": "V"},
                ],
                "device_point_list": [
                    {"device_point": {"uuid": 4841885, "p96": "60.2", "device_name": "Inv"}},
                ],
            },
        }
    )

    data = await plants.async_get_device_realtime("123", DeviceType.INVERTER, ps_key_list=["dev-1"])

    assert "4841885" in data
    assert data["4841885"]["96"]["value"] == 60.2


@pytest.mark.asyncio
async def test_device_realtime_requests_only_the_given_points(auth, plants):
    """With explicit extra points, the request sends only those — not the 74 plant points.

    getDeviceRealTimeData caps point_id_list at 100 (result_code 010); padding a device
    query with the plant measure points pushed larger requests (e.g. an inverter's
    diagnostic set) over the limit and failed the whole call.
    """
    assert len(plants.measure_points) > 40  # the base plant points that must NOT be sent
    auth.request.return_value = _mock_response(
        {"result_code": "1", "result_msg": "success", "result_data": {"point_dict": [], "device_point_list": []}}
    )

    await plants.async_get_device_realtime(
        "123", DeviceType.INVERTER, ps_key_list=["dev-1"], extra_measure_points={"96": "string_1_voltage"}
    )

    sent_points = auth.request.call_args.args[1]["point_id_list"]
    assert sent_points == ["96"]  # only the requested point, well under the 100 cap


@pytest.mark.asyncio
async def test_device_realtime_gracefully_degrades_on_404(auth, plants):
    """Device realtime endpoint returns {} when the upstream endpoint is absent."""
    auth.request.return_value = _mock_response(
        {"result_code": "E996", "result_msg": "api not found", "result_data": None}, status=404
    )

    data = await plants.async_get_device_realtime("123", 7, ps_key_list=["dev-1"])

    assert data == {}


@pytest.mark.asyncio
async def test_device_realtime_swallows_known_api_errors(auth, plants):
    """Known soft errors from the device endpoint are treated as "unsupported"."""
    auth.request.return_value = _mock_response(
        {"result_code": "E996", "result_msg": "api not found", "result_data": None}
    )

    data = await plants.async_get_device_realtime("123", DeviceType.METER, ps_key_list=["dev-1"])

    assert data == {}


@pytest.mark.asyncio
async def test_device_realtime_raises_on_unexpected_error(auth, plants):
    """Unexpected errors still raise PySolarCloudException."""
    from pysolarcloud import PySolarCloudException

    auth.request.return_value = _mock_response(
        {"result_code": "E00003", "result_msg": "The token is invalid or has expired", "result_data": None}
    )

    with pytest.raises(PySolarCloudException):
        await plants.async_get_device_realtime("123", DeviceType.METER, ps_key_list=["dev-1"])


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

    await plants.async_get_device_realtime("123", "7", ps_key_list=["dev-1"])
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

    await plants.async_get_device_realtime("123", DeviceType.METER, ps_key_list=["dev-1"])
    body = auth.request.call_args.args[1]
    assert body["point_id_list"] == list(Plants.measure_points.keys())


@pytest.mark.asyncio
async def test_device_realtime_requests_extra_points_without_plant_points(auth, plants):
    """Device realtime requests the given extra point IDs and omits the plant points.

    The plant measure points don't apply to a single device and would only eat into the
    100-point cap, so an explicit extra-point request sends just those.
    """
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

    await plants.async_get_device_realtime(
        "123", DeviceType.METER, ps_key_list=["dev-1"], extra_measure_points={"11111": "ev_power"}
    )
    body = auth.request.call_args.args[1]
    assert "11111" in body["point_id_list"]
    assert "83033" not in body["point_id_list"]  # a plant measure point — must NOT be sent


@pytest.mark.asyncio
async def test_device_realtime_sends_ps_key_list(auth, plants):
    """The request carries ps_key_list + device_type (not the old ps_id-only shape).

    Regression: without ps_key_list/sn_list the API rejects the call with
    result_code 009 "Parameters ps_key_list,sn_list cannot be empty at the same time!".
    """
    auth.request.return_value = _mock_response(
        {"result_code": "1", "result_msg": "success", "result_data": {"point_dict": [], "device_point_list": []}}
    )

    await plants.async_get_device_realtime("123", DeviceType.INVERTER, ps_key_list=["123_1_1_1"])

    body = auth.request.call_args.args[1]
    assert body["ps_key_list"] == ["123_1_1_1"]
    assert body["device_type"] == "1"
    assert "ps_id" not in body


@pytest.mark.asyncio
async def test_device_realtime_discovers_ps_keys_when_omitted(auth, plants):
    """When ps_key_list is omitted, device keys are discovered from the device list."""
    plants.async_get_plant_devices = AsyncMock(
        return_value=[
            {"uuid": "inv-1", "ps_key": "123_1_1_1", "device_type": DeviceType.INVERTER},
            {"uuid": "inv-2", "ps_key": "123_1_1_2", "device_type": DeviceType.INVERTER},
        ]
    )
    auth.request.return_value = _mock_response(
        {"result_code": "1", "result_msg": "success", "result_data": {"point_dict": [], "device_point_list": []}}
    )

    await plants.async_get_device_realtime("123", DeviceType.INVERTER)

    plants.async_get_plant_devices.assert_awaited_once()
    body = auth.request.call_args.args[1]
    assert body["ps_key_list"] == ["123_1_1_1", "123_1_1_2"]


@pytest.mark.asyncio
async def test_device_realtime_returns_empty_when_no_devices(auth, plants):
    """No device of the requested type -> empty dict and no realtime API call."""
    plants.async_get_plant_devices = AsyncMock(return_value=[])

    data = await plants.async_get_device_realtime("123", DeviceType.INVERTER)

    assert data == {}
    auth.request.assert_not_called()


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


@pytest.mark.asyncio
async def test_get_plants_returns_page_list(auth, plants):
    """async_get_plants posts to queryPowerStationList and returns result_data.pageList."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_msg": "success",
            "result_data": {"pageList": [{"ps_id": "123", "ps_name": "Home"}], "rowCount": 1},
        }
    )

    result = await plants.async_get_plants()

    uri = auth.request.call_args.args[0]
    body = auth.request.call_args.args[1]
    assert uri == "/openapi/platform/queryPowerStationList"
    assert body == {"page": 1, "size": 100}
    assert result == [{"ps_id": "123", "ps_name": "Home"}]


@pytest.mark.asyncio
async def test_get_plants_raises_on_error(auth, plants):
    """A result_code != "1" raises PySolarCloudException with .error == the code."""
    from pysolarcloud import PySolarCloudException

    auth.request.return_value = _mock_response(
        {"result_code": "E00003", "result_msg": "The token is invalid or has expired", "result_data": None}
    )

    with pytest.raises(PySolarCloudException) as exc:
        await plants.async_get_plants()
    assert exc.value.error == "E00003"
    assert exc.value.result_msg == "The token is invalid or has expired"


@pytest.mark.asyncio
async def test_get_plant_details_scalar_id_returns_data_list(auth, plants):
    """A scalar plant id is sent verbatim and result_data.data_list is returned."""
    auth.request.return_value = _mock_response(
        {"result_code": "1", "result_msg": "success", "result_data": {"data_list": [{"ps_id": "123"}]}}
    )

    result = await plants.async_get_plant_details("123")

    uri = auth.request.call_args.args[0]
    body = auth.request.call_args.args[1]
    assert uri == "/openapi/platform/getPowerStationDetail"
    assert body["ps_ids"] == "123"
    assert result == [{"ps_id": "123"}]


@pytest.mark.asyncio
async def test_get_plant_details_list_ids_are_comma_joined(auth, plants):
    """A list of plant ids is comma-joined into ps_ids."""
    auth.request.return_value = _mock_response(
        {"result_code": "1", "result_msg": "success", "result_data": {"data_list": []}}
    )

    await plants.async_get_plant_details(["123", "456"])

    body = auth.request.call_args.args[1]
    assert body["ps_ids"] == "123,456"


@pytest.mark.asyncio
async def test_get_plant_details_raises_on_error(auth, plants):
    """A result_code != "1" raises PySolarCloudException with .error == the code."""
    from pysolarcloud import PySolarCloudException

    auth.request.return_value = _mock_response(
        {"result_code": "009", "result_msg": "rate limited", "result_data": None}
    )

    with pytest.raises(PySolarCloudException) as exc:
        await plants.async_get_plant_details("123")
    assert exc.value.error == "009"


@pytest.mark.asyncio
async def test_get_plant_devices_converts_type_and_fault_status_to_enums(auth, plants):
    """Known device_type / dev_fault_status codes are converted to their enums."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_msg": "success",
            "result_data": {
                "pageList": [
                    {"device_name": "Inv1", "device_type": 1, "dev_fault_status": 4},
                ]
            },
        }
    )

    devices = await plants.async_get_plant_devices("123")

    uri = auth.request.call_args.args[0]
    body = auth.request.call_args.args[1]
    assert uri == "/openapi/platform/getDeviceListByPsId"
    assert body["ps_id"] == "123"
    assert "device_type_list" not in body
    assert devices[0]["device_type"] is DeviceType.INVERTER
    assert devices[0]["dev_fault_status"] is DeviceFaultStaus.NORMAL


@pytest.mark.asyncio
async def test_get_plant_devices_forwards_device_type_filter(auth, plants):
    """device_types are normalised to their numeric string form in device_type_list."""
    auth.request.return_value = _mock_response(
        {"result_code": "1", "result_msg": "success", "result_data": {"pageList": []}}
    )

    await plants.async_get_plant_devices("123", device_types=[DeviceType.INVERTER, 7])

    body = auth.request.call_args.args[1]
    assert body["device_type_list"] == ["1", "7"]


@pytest.mark.asyncio
async def test_get_plant_devices_leaves_unknown_codes_untouched(auth, plants):
    """Device/fault codes outside the enums pass through unchanged (no crash)."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_msg": "success",
            "result_data": {"pageList": [{"device_type": 999, "dev_fault_status": 0}]},
        }
    )

    devices = await plants.async_get_plant_devices("123")
    assert devices[0]["device_type"] == 999
    assert devices[0]["dev_fault_status"] == 0


@pytest.mark.asyncio
async def test_get_plant_devices_raises_on_error(auth, plants):
    """A result_code != "1" raises PySolarCloudException with .error == the code."""
    from pysolarcloud import PySolarCloudException

    auth.request.return_value = _mock_response(
        {"result_code": "E00003", "result_msg": "The token is invalid or has expired", "result_data": None}
    )

    with pytest.raises(PySolarCloudException) as exc:
        await plants.async_get_plant_devices("123")
    assert exc.value.error == "E00003"


@pytest.mark.asyncio
async def test_realtime_data_keeps_non_numeric_values_as_strings(auth, plants):
    """A non-numeric point value is preserved verbatim rather than raising."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_msg": "success",
            "result_data": {
                "point_dict": [{"point_id": "83033", "point_name": "Power", "point_unit": "W"}],
                "device_point_list": [{"ps_id": "123", "p83033": "N/A"}],
            },
        }
    )

    data = await plants.async_get_realtime_data("123")
    assert data["123"]["power"]["value"] == "N/A"


@pytest.mark.asyncio
async def test_device_realtime_skips_devices_without_uuid(auth, plants):
    """A device entry lacking uuid/device_id is skipped rather than keyed under ""."""
    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_msg": "success",
            "result_data": {
                "point_dict": [{"point_id": "83033", "point_name": "Power", "point_unit": "W"}],
                "device_point_list": [
                    {"p83033": "1000"},  # no uuid -> skipped
                    {"uuid": "dev-9", "p83033": "2000"},
                ],
            },
        }
    )

    data = await plants.async_get_device_realtime("123", DeviceType.METER, ps_key_list=["dev-9"])
    assert set(data.keys()) == {"dev-9"}
    assert data["dev-9"]["power"]["value"] == 2000.0


@pytest.mark.asyncio
async def test_historical_data_named_measure_points_are_mapped(auth, plants):
    """Named measure points are translated to their numeric point IDs in the points CSV."""
    from datetime import datetime

    auth.request.return_value = _mock_response(
        {"result_code": "1", "result_msg": "success", "result_data": {"point_dict": []}}
    )

    await plants.async_get_historical_data("123", datetime(2024, 1, 1, 0, 0, 0), measure_points=["power", "83024"])

    body = auth.request.call_args.args[1]
    # "power" -> 83033, "83024" left as-is; both prefixed with "p" in the points CSV.
    assert body["points"] == "p83033,p83024"


@pytest.mark.asyncio
async def test_historical_data_parses_time_series(auth, plants):
    """Historical series frames are parsed into per-plant lists with timestamps."""
    from datetime import datetime

    auth.request.return_value = _mock_response(
        {
            "result_code": "1",
            "result_msg": "success",
            "result_data": {
                "point_dict": [{"point_id": "83033", "point_name": "Power", "point_unit": "W"}],
                "123": [
                    {"time_stamp": "20240101000000", "p83033": "1000"},
                    {"time_stamp": "20240101010000", "p83033": "1500"},
                ],
            },
        }
    )

    data = await plants.async_get_historical_data("123", datetime(2024, 1, 1, 0, 0, 0))

    series = data["123"]
    assert series[0]["code"] == "power"
    assert series[0]["value"] == 1000.0
    assert series[0]["unit"] == "W"
    assert series[0]["timestamp"] == datetime(2024, 1, 1, 0, 0, 0)
    assert series[1]["value"] == 1500.0


@pytest.mark.asyncio
async def test_historical_data_raises_on_error(auth, plants):
    """A result_code != "1" raises PySolarCloudException with .error == the code."""
    from datetime import datetime

    from pysolarcloud import PySolarCloudException

    auth.request.return_value = _mock_response(
        {"result_code": "E00003", "result_msg": "The token is invalid or has expired", "result_data": None}
    )

    with pytest.raises(PySolarCloudException) as exc:
        await plants.async_get_historical_data("123", datetime(2024, 1, 1, 0, 0, 0))
    assert exc.value.error == "E00003"


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


def _points_present_response(id_value_map, *, key="uuid", ident="dev-1"):
    """Build a side_effect that answers each chunk with only the points it asked for.

    Robust to how many chunks the point set is split into: each request returns the
    subset of ``id_value_map`` whose IDs appear in that chunk's point_id_list.
    """

    def _side_effect(uri, body, lang=None):
        ids = set(body["point_id_list"])
        present = {pid: val for pid, val in id_value_map.items() if pid in ids}
        device = {key: ident, **{f"p{pid}": val for pid, val in present.items()}}
        return _mock_response(
            {
                "result_code": "1",
                "result_msg": "success",
                "result_data": {
                    "point_dict": [{"point_id": pid, "point_name": pid, "point_unit": "W"} for pid in present],
                    "device_point_list": [device],
                },
            }
        )

    return _side_effect


@pytest.mark.asyncio
async def test_device_realtime_chunks_over_100_points(auth, plants):
    """>100 point IDs are split into ≤100-point requests and the results merged.

    getDeviceRealTimeData rejects a point_id_list longer than 100 (result_code 010),
    so a hybrid inverter's diagnostics plus user extras must be requested in chunks.
    """
    extra = {str(i): f"code_{i}" for i in range(150)}
    # Sentinel points at the start and past the first chunk boundary.
    auth.request.side_effect = _points_present_response({"0": "10", "100": "20"})

    data = await plants.async_get_device_realtime(
        "123", DeviceType.INVERTER, ps_key_list=["dev-1"], extra_measure_points=extra
    )

    # Multiple requests, each within the 100-point cap.
    assert auth.request.call_count == 2
    for call in auth.request.call_args_list:
        assert len(call.args[1]["point_id_list"]) <= 100
    # Points from both chunks are merged onto the same device.
    assert data["dev-1"]["code_0"]["value"] == 10.0
    assert data["dev-1"]["code_100"]["value"] == 20.0


@pytest.mark.asyncio
async def test_device_realtime_single_request_when_within_cap(auth, plants):
    """≤100 points still make exactly one request (no behavioural change)."""
    auth.request.return_value = _mock_response(
        {"result_code": "1", "result_msg": "success", "result_data": {"point_dict": [], "device_point_list": []}}
    )

    await plants.async_get_device_realtime(
        "123", DeviceType.INVERTER, ps_key_list=["dev-1"], extra_measure_points={"96": "string_1_voltage"}
    )

    assert auth.request.call_count == 1


@pytest.mark.asyncio
async def test_realtime_data_chunks_over_100_points(auth, plants):
    """Plant realtime also chunks a >100-point request and merges per plant."""
    extra = {str(i): f"code_{i}" for i in range(200)}
    auth.request.side_effect = _points_present_response({"0": "1", "100": "2"}, key="ps_id", ident="123")

    data = await plants.async_get_realtime_data("123", extra_measure_points=extra)

    assert auth.request.call_count >= 2
    for call in auth.request.call_args_list:
        assert len(call.args[1]["point_id_list"]) <= 100
    assert data["123"]["code_0"]["value"] == 1.0
    assert data["123"]["code_100"]["value"] == 2.0


if __name__ == "__main__":
    pytest.main([__file__])
