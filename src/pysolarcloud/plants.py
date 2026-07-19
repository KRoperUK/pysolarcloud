from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, cast

from . import _LOGGER, AbstractAuth, PySolarCloudException

# iSolarCloud's ``getPowerStationPointMinuteDataList`` accepts arbitrary
# ``start_time_stamp`` / ``end_time_stamp`` values, but wide windows (> a few
# hours) return incomplete series or timeouts in practice. 3 hours matches the
# per-call chunk sungrow-hass has been running against production for backfill
# without observed truncation, so use it as the default paging step for
# :meth:`Plants.async_iter_historical_data`.
_DEFAULT_HISTORICAL_CHUNK = timedelta(hours=3)

# result_code values from the per-device realtime endpoint that mean "this endpoint / target is
# not available for this account" rather than a genuine failure. When we see one of these (or an
# HTTP 404/405) we degrade to an empty dict so callers can feature-detect gracefully.
# E994 = "system not found", E996 = "api not found" (see Appendix 2: API Error Code Definitions).
_DEVICE_ENDPOINT_MISSING_CODES = frozenset({"E994", "E996"})

# The realtime endpoints reject a point_id_list longer than 100 entries (result_code
# 010: "Parameters point_id_list size is over 100"). Larger point sets (a hybrid
# inverter's diagnostics plus user-supplied extras) are requested in chunks and merged.
_MAX_POINT_IDS_PER_REQUEST = 100


def _chunked[T](seq: list[T], size: int) -> Iterator[list[T]]:
    """Yield successive ``size``-length chunks of ``seq`` (a single chunk when it fits)."""
    for start in range(0, len(seq), size):
        yield seq[start : start + size]


class DeviceType(Enum):
    """Enum for the device types used by async_get_plant_devices."""

    INVERTER = 1
    CONTAINER = 2
    GRID_CONNECTION_POINT = 3
    COMBINER_BOX = 4
    METEO_STATION = 5
    TRANSFORMER = 6
    METER = 7
    UPS = 8
    DATA_LOGGER = 9
    STRING = 10
    PLANT = 11
    CIRCUIT_PROTECTION = 12
    SPLITTING_DEVICE = 13
    ENERGY_STORAGE_SYSTEM = 14
    SAMPLING_DEVICE = 15
    EMU = 16
    UNIT = 17
    TEMPERATURE_AND_HUMIDITY_SENSOR = 18
    INTELLIGENT_POWER_DISTRIBUTION_CABINET = 19
    DISPLAY_DEVICE = 20
    AC_POWER_DISTRIBUTED_CABINET = 21
    COMMUNICATION_MODULE = 22
    SYSTEM_BMS = 23
    ARRAY_BMS = 24
    DC_DC = 25
    ENERGY_MANAGEMENT_SYSTEM = 26
    TRACKING_SYSTEM = 27
    WIND_ENERGY_CONVERTER = 28
    SVG = 29
    PT_CABINET = 30
    BUS_PROTECTION = 31
    CLEANING_DEVICE = 32
    DIRECT_CURRENT_CABINET = 33
    PUBLIC_MEASUREMENT_AND_CONTROL = 34
    # 37 is documented as "PCS" in Appendix 1. It was originally added here as
    # ENERGY_STORAGE_SYSTEM_2; that name is kept for backwards compatibility and PCS is
    # provided below as an alias for the same member.
    ENERGY_STORAGE_SYSTEM_2 = 37
    OPTIMIZER = 41
    BATTERY = 43
    BATTERY_CLUSTER_MANAGEMENT_UNIT = 44
    LOCAL_CONTROLLER = 45
    CHARGER = 51
    BATTERY_SYSTEM_CONTROLLER = 52
    MICROINVERTER = 55
    DIESEL_GENERATOR = 63
    PCS = 37  # alias of ENERGY_STORAGE_SYSTEM_2 (Appendix 1 name)


class DeviceFaultStaus(Enum):
    """Enum for the device fault status used by async_get_plant_devices."""

    FAULT = 1
    ALARM = 2
    NORMAL = 4


class Plants:
    """Class to interact with the plants API."""

    def __init__(self, auth: AbstractAuth, *, lang: str = "_en_US"):
        """Initialize the plants."""
        self.auth = auth
        self.lang = lang

    async def async_get_plants(self) -> list[dict[str, Any]]:
        """Return the list of plants accessible to the user."""
        uri = "/openapi/platform/queryPowerStationList"
        res = await self.auth.request(uri, {"page": 1, "size": 100})
        res.raise_for_status()
        data = await res.json()
        if data.get("result_code") != "1":
            _LOGGER.error("Error response from %s: %s", uri, data)
            raise PySolarCloudException.from_response(data)
        plants = [plant for plant in data["result_data"]["pageList"]]
        _LOGGER.debug("async_get_plants: %s", plants)
        return plants

    async def async_get_plant_details(self, plant_id: str | list[str]) -> list[dict[str, Any]]:
        """Return details about one or more plants."""
        ps = ",".join(plant_id) if isinstance(plant_id, list) else plant_id
        uri = "/openapi/platform/getPowerStationDetail"
        res = await self.auth.request(uri, {"ps_ids": ps})
        res.raise_for_status()
        data = await res.json()
        if data.get("result_code") != "1":
            _LOGGER.error("Error response from %s: %s", uri, data)
            raise PySolarCloudException.from_response(data)
        plants: list[dict[str, Any]] = data["result_data"]["data_list"]
        _LOGGER.debug("async_get_plant_details: %s", plants)
        return plants

    async def async_get_plant_devices(
        self, plant_id: str, *, device_types: list[DeviceType | int] | None = None
    ) -> list[dict[str, Any]]:
        """Return details about the devices for a plant."""
        if device_types is None:
            device_types = []
        uri = "/openapi/platform/getDeviceListByPsId"
        params = {"ps_id": plant_id, "page": 1, "size": 100}
        if device_types:
            params["device_type_list"] = [str(d.value) if isinstance(d, DeviceType) else str(d) for d in device_types]
        res = await self.auth.request(uri, params)
        res.raise_for_status()
        data = await res.json()
        if data.get("result_code") != "1":
            _LOGGER.error("Error response from %s: %s", uri, data)
            raise PySolarCloudException.from_response(data)
        devices: list[dict[str, Any]] = data["result_data"]["pageList"]
        for device in devices:
            # Convert the device type and fault status to enums
            if device["device_type"] in DeviceType:
                device["device_type"] = DeviceType(device["device_type"])
            if device["dev_fault_status"] in DeviceFaultStaus:
                device["dev_fault_status"] = DeviceFaultStaus(device["dev_fault_status"])
        _LOGGER.debug("async_get_plant_devices: %s", devices)
        return devices

    async def async_get_realtime_data(
        self,
        plant_id: str | list[str],
        *,
        measure_points: list[str] | None = None,
        extra_measure_points: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Return the latest realtime data from one or more plants.

        plant_id: str | list[str] - The ID of the plant or a list of plant IDs.
        measure_points: list[str] - A list of measure points to return. If None, all measure points are returned.
        extra_measure_points: dict[str, str] - Mapping of additional point_id -> code pairs to
            request alongside the defaults. Useful for surfacing fields the upstream library
            hasn't catalogued (e.g. newer battery or EV-charger point IDs). Returned data points
            use the codes supplied here verbatim.

        Data is returned as a dictionary of dictionaries:
        {
            plant_id: {
                measure_point_code: {
                    "id": str, # Numerical identifier of the measure point
                    "code": str, # Readable code of the measure point (see measure_points dict)
                    "value": float | str,
                    "unit": str,
                    "name": str, # Name of the measure point (in the specified language)
                }
            }
        }
        iSolarCloud data is updated every 5 minutes so polling more frequently than that is not useful.
        """
        ps = plant_id if isinstance(plant_id, list) else [plant_id]
        # Merge the canonical measure_points map with any caller-supplied extras for this call
        # only — we deliberately do not mutate the class-level dict so concurrent callers and
        # other Plants instances see the upstream defaults.
        effective_points = dict(self.measure_points)
        if extra_measure_points:
            effective_points.update(extra_measure_points)
        if measure_points is None:
            ms = list(effective_points.keys())
        else:
            measure_points_map = {v: k for k, v in effective_points.items()}
            ms = [m if m.isdigit() else measure_points_map[m] for m in measure_points]
        uri = "/openapi/platform/getPowerStationRealTimeData"
        # The endpoint caps point_id_list at 100; request in chunks and merge per plant
        # so a large measure-point set (defaults plus user extras) doesn't fail the call.
        plants: dict[str, Any] = {}
        for chunk in _chunked(ms, _MAX_POINT_IDS_PER_REQUEST):
            res = await self.auth.request(
                uri, {"ps_id_list": ps, "point_id_list": chunk, "is_get_point_dict": "1"}, lang=self.lang
            )
            res.raise_for_status()
            res = await res.json()
            if res.get("result_code") != "1":
                _LOGGER.error("Error response from %s: %s", uri, res)
                raise PySolarCloudException.from_response(res)
            point_dict = dict([(str(point["point_id"]), point) for point in res["result_data"]["point_dict"]])
            for plant in res["result_data"]["device_point_list"]:
                data = [
                    self._format_measure_point(k[1:], v, point_dict, effective_points)
                    for k, v in plant.items()
                    if k[0] == "p" and k[1:].isdigit()
                ]
                plants.setdefault(str(plant["ps_id"]), {}).update({d["code"]: d for d in data})
        _LOGGER.debug("async_get_realtime_data: %s", plants)
        return plants

    async def async_get_device_realtime(
        self,
        plant_id: str,
        device_type: DeviceType | int | str,
        *,
        ps_key_list: list[str] | None = None,
        extra_measure_points: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Best-effort device-level realtime fetch for non-inverter devices.

        The iSolarCloud plant realtime endpoint aggregates all points at the plant level and does
        not separate per-device data for chargers, batteries, etc. Some accounts / regions expose
        a per-device endpoint; when it is not available, this method returns an empty dict rather
        than raising, so callers can feature-detect gracefully.

        ``getDeviceRealTimeData`` keys its result per device and requires the specific devices to
        query: ``ps_key_list`` (or ``sn_list``) must be supplied or the API rejects the call with
        ``result_code`` ``009``. Callers that already hold the device list should pass
        ``ps_key_list`` (each device's ``ps_key`` from :meth:`async_get_plant_devices`) to avoid an
        extra lookup; when it is omitted, the matching devices are discovered here. If no
        dispatchable device of ``device_type`` exists, an empty dict is returned.

        Returns a dict keyed by device uuid, each value being the same measure-point structure as
        :meth:`async_get_realtime_data`.
        """
        type_id = device_type.value if isinstance(device_type, DeviceType) else int(device_type)
        if ps_key_list is None:
            devices = await self.async_get_plant_devices(plant_id, device_types=[type_id])
            ps_key_list = [str(d["ps_key"]) for d in devices if d.get("ps_key")]
        if not ps_key_list:
            return {}
        # When the caller specifies the exact points it wants, request only those: the
        # 74 plant-level measure points don't apply to an individual device and would
        # just waste the request budget. getDeviceRealTimeData caps point_id_list at 100
        # (result_code 010), so padding every device query with the plant points pushed
        # larger requests (e.g. an inverter's diagnostic set) over the limit and made the
        # whole call fail. Fall back to the plant points only for feature-detection when
        # no explicit points are given.
        effective_points = dict(extra_measure_points) if extra_measure_points else dict(self.measure_points)
        uri = "/openapi/platform/getDeviceRealTimeData"
        # getDeviceRealTimeData caps point_id_list at 100 (result_code 010); a hybrid
        # inverter's diagnostic set plus user extras can exceed that, so request the
        # points in chunks and merge the per-device results.
        out: dict[str, dict[str, Any]] = {}
        for chunk in _chunked(list(effective_points.keys()), _MAX_POINT_IDS_PER_REQUEST):
            res = await self.auth.request(
                uri,
                {
                    "ps_key_list": ps_key_list,
                    "device_type": str(type_id),
                    "point_id_list": chunk,
                    "is_get_point_dict": "1",
                },
                lang=self.lang,
            )
            # Many accounts do not have this endpoint; treat transport / API errors as "unsupported"
            # and let the caller decide how to surface that. We deliberately swallow only the
            # "endpoint missing" class of failure, not generic 4xx/5xx.
            if res.status in (404, 405):
                _LOGGER.debug("Device realtime endpoint unavailable for plant %s type %s", plant_id, type_id)
                return {}
            res = await res.json()
            if res.get("result_code") != "1":
                if res.get("result_code") in _DEVICE_ENDPOINT_MISSING_CODES:
                    _LOGGER.debug("Device realtime endpoint rejected request: %s", res)
                    return {}
                _LOGGER.error("Error response from %s: %s", uri, res)
                raise PySolarCloudException.from_response(res)
            point_dict_items = res.get("result_data", {}).get("point_dict", []) or []
            point_dict = {str(p["point_id"]): p for p in point_dict_items}
            device_lists = res.get("result_data", {}).get("device_point_list", []) or []
            for entry in device_lists:
                # getDeviceRealTimeData nests the device fields (uuid + p<id> values) under a
                # "device_point" key; older/other responses put them at the top level. Unwrap
                # so the uuid and point values are read from wherever they actually are —
                # otherwise every device is skipped (uuid=None) and the result is empty.
                device = entry.get("device_point", entry) if isinstance(entry, dict) else entry
                uuid = str(device.get("uuid") or device.get("device_id") or "")
                if not uuid:
                    continue
                data = [
                    self._format_measure_point(k[1:], v, point_dict, effective_points)
                    for k, v in device.items()
                    if k[0] == "p" and k[1:].isdigit()
                ]
                out.setdefault(uuid, {}).update({d["code"]: d for d in data})
        return out

    async def async_get_dev_property_point_value(
        self, plant_id: str, device_type: DeviceType | int | str, point_ids: list[str]
    ) -> dict[str, Any]:
        """Return device property point values (e.g. per-device charge/discharge/feed-in limits).

        Appendix 10 (Control Parameter Definitions) documents that the dispatch bounds for several
        control parameters must be read back through ``getDevPropertyPointValue`` rather than being
        static, for example:

        * point ``18290`` — Max. Charging Power upper-limit range (bounds param_code ``10091``)
        * point ``18291`` — Max. Discharging Power upper-limit range (bounds param_code ``10092``)
        * point ``29046`` — Charging/Discharging Power upper limit in external dispatch mode
          (bounds param_code ``10083``)

        plant_id: str - The ID of the plant.
        device_type: DeviceType | int | str - The device type to query (e.g. an Energy Storage
            System). Normalised to its numeric string form.
        point_ids: list[str] - The property point IDs to read.

        Returns the raw ``result_data`` object from the API (shape is device/point dependent).

        .. note::
            The iSolarCloud docs reference this endpoint by name and by point ID but do not publish
            a full request schema. The request field names (``ps_id``, ``device_type``,
            ``point_id_list``) mirror the sibling ``getDeviceRealTimeData`` endpoint and are
            **unverified against a live device**.
        """
        type_id = device_type.value if isinstance(device_type, DeviceType) else int(device_type)
        uri = "/openapi/platform/getDevPropertyPointValue"
        res = await self.auth.request(
            uri,
            {
                "ps_id": str(plant_id),
                "device_type": str(type_id),
                "point_id_list": [str(p) for p in point_ids],
            },
            lang=self.lang,
        )
        res.raise_for_status()
        res = await res.json()
        if res.get("result_code") != "1":
            _LOGGER.error("Error response from %s: %s", uri, res)
            raise PySolarCloudException.from_response(res)
        _LOGGER.debug("async_get_dev_property_point_value: %s", res.get("result_data"))
        return cast(dict[str, Any], res.get("result_data") or {})

    async def async_get_open_point_info(self, device_type: DeviceType | int | str | None = None) -> dict[str, Any]:
        """Return the available open measuring-point definitions.

        The common measuring-point pages (e.g. "Common Plant Measuring Points") note that additional
        open measuring-point definitions can be discovered through the ``getOpenPointInfo`` endpoint.
        This wrapper exposes that catalogue so callers can feature-detect point IDs beyond the
        static :attr:`measure_points` map.

        device_type: DeviceType | int | str | None - Optionally scope the definitions to a single
            device type. When omitted, the device-type filter is not sent.

        Returns the raw ``result_data`` object from the API (a list/map of point definitions).

        .. note::
            The docs reference this endpoint by name only and do not publish a full request schema.
            The ``device_type`` request field mirrors the other device endpoints and is
            **unverified against a live device**.
        """
        uri = "/openapi/platform/getOpenPointInfo"
        params: dict[str, Any] = {}
        if device_type is not None:
            type_id = device_type.value if isinstance(device_type, DeviceType) else int(device_type)
            params["device_type"] = str(type_id)
        res = await self.auth.request(uri, params, lang=self.lang)
        res.raise_for_status()
        res = await res.json()
        if res.get("result_code") != "1":
            _LOGGER.error("Error response from %s: %s", uri, res)
            raise PySolarCloudException.from_response(res)
        _LOGGER.debug("async_get_open_point_info: %s", res.get("result_data"))
        return cast(dict[str, Any], res.get("result_data") or {})

    async def async_get_historical_data(
        self,
        plant_id: str | list[str],
        start_time: datetime,
        end_time: datetime | None = None,
        *,
        measure_points: list[str] | None = None,
        interval: timedelta = timedelta(minutes=60),
    ) -> dict[str, Any]:
        """Return historical data from one or more plants.

        plant_id: str | list[str] - The ID of the plant or a list of plant IDs.
        start_time: datetime - The start time of the data to retrieve.
        end_time: datetime - The end time of the data to retrieve. If end_time is not specified, 3 hours of data is returned.
        measure_points: list[str] - A list of measure points to return. If None, all measure points are returned.
        interval: timedelta - The interval in minutes between data points. The minimum interval is 1 minute. Default is 60 minutes.
        Data is returned as a dictionary of lists:
        {
            plant_id: [
                {
                    "timestamp": datetime,
                    "id": str, # Numerical identifier of the measure point
                    "code": str, # Readable code of the measure point (see measure_points dict)
                    "value": float | str,
                    "unit": str,
                    "name": str, # Name of the measure point (in the specified language)
                }
            ]
        }
        """
        ps = plant_id if isinstance(plant_id, list) else [plant_id]
        if measure_points is None:
            ms = list(self.measure_points.keys())
        else:
            measure_points_map = {v: k for k, v in self.measure_points.items()}
            ms = [m if m.isdigit() else measure_points_map[m] for m in measure_points]
        if end_time is None:
            end_time = start_time + timedelta(hours=3)
        TS_FORMAT = "%Y%m%d%H%M%S"
        uri = "/openapi/platform/getPowerStationPointMinuteDataList"
        params = {
            "ps_id_list": ps,
            "points": ",".join(["p" + m for m in ms]),
            "is_get_point_dict": "1",
            "start_time_stamp": start_time.strftime(TS_FORMAT),
            "end_time_stamp": end_time.strftime(TS_FORMAT),
            "minute_interval": str(interval.seconds // 60),
        }
        res = await self.auth.request(uri, params, lang=self.lang)
        res.raise_for_status()
        res = await res.json()
        if res.get("result_code") != "1":
            _LOGGER.error("Error response from %s: %s", uri, res)
            raise PySolarCloudException.from_response(res)
        point_dict = dict([(str(point["point_id"]), point) for point in res["result_data"]["point_dict"]])
        plants = {}
        for ps_id, plant in res["result_data"].items():
            if ps_id == "point_dict":
                continue
            series = []
            for frame in plant:
                ts = datetime.strptime(frame["time_stamp"], TS_FORMAT)
                for k, v in frame.items():
                    if k == "time_stamp":
                        continue
                    data = self._format_measure_point(k[1:], v, point_dict, self.measure_points)
                    data["timestamp"] = ts
                    series.append(data)
            plants[str(ps_id)] = series
        _LOGGER.debug("async_get_historical_data: %s", plants)
        return plants

    async def async_iter_historical_data(
        self,
        plant_id: str,
        start_time: datetime,
        end_time: datetime,
        *,
        measure_points: list[str] | None = None,
        interval: timedelta = timedelta(minutes=60),
        chunk_window: timedelta = _DEFAULT_HISTORICAL_CHUNK,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield historical rows across ``[start_time, end_time)``, transparently paging.

        A thin generator over :meth:`async_get_historical_data` that walks a
        bounded time window in ``chunk_window``-sized steps. Consumers no
        longer need to track a ``start_time_stamp`` cursor themselves — the
        implementation the sungrow-hass integration's backfill manager has
        been re-implementing lives here now.

        plant_id: str — single plant identifier. Multi-plant callers should iterate
            across the list themselves; the API's per-call point cap and observed
            per-chunk truncation make wrapping N plants into one generator lossier.
        start_time / end_time: absolute ``[start, end)`` bounds.
        measure_points: point codes or numeric IDs, forwarded to
            :meth:`async_get_historical_data`.
        interval: sampling interval within each chunk.
        chunk_window: maximum span of a single underlying call. Larger values are
            accepted by the API but return truncated series in practice; the
            3-hour default matches what backfill has been running against
            production without observed data loss.

        Rows come out in ascending chronological order (chunks are walked in
        order; within-chunk ordering is whatever the API returns, which is
        chronological in practice). An empty window yields nothing. A
        ``chunk_window`` of zero or negative raises :class:`ValueError` so the
        loop can't spin.
        """
        if chunk_window <= timedelta(0):
            raise ValueError("chunk_window must be positive")
        if end_time <= start_time:
            return
        cursor = start_time
        while cursor < end_time:
            chunk_end = min(cursor + chunk_window, end_time)
            page = await self.async_get_historical_data(
                plant_id,
                cursor,
                chunk_end,
                measure_points=measure_points,
                interval=interval,
            )
            # ``async_get_historical_data`` groups rows per plant; a single-plant
            # call returns exactly one key. Iterate defensively so a future
            # multi-plant response wouldn't silently drop rows.
            for rows in page.values():
                for row in rows:
                    yield row
            cursor = chunk_end

    def _format_measure_point(
        self,
        point_id: str,
        point_value: str,
        point_dict: dict[str, Any],
        measure_points: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        v: float | str | None
        try:
            v = float(point_value) if point_value is not None else None
        except ValueError:
            v = point_value
        code_map = measure_points if measure_points is not None else self.measure_points
        return {
            "id": point_id,
            "code": code_map.get(point_id, point_id),
            "value": v,
            "unit": point_dict.get(point_id, {}).get("point_unit", None),
            "name": point_dict.get(point_id, {}).get("point_name", None),
        }

    measure_points = {
        "83022": "daily_yield",  # Wh
        "83024": "total_yield",  # Wh
        "83033": "power",  # W
        "83019": "power_fraction",  # Plant Power/Installed Power of Plant
        "83006": "meter_daily_yield",  # Wh
        "83020": "meter_total_yield",  # Wh
        "83011": "meter_e_daily_consumption",  # Wh
        "83021": "accumulative_power_consumption_by_meter",  # Wh
        "83032": "meter_ac_power",  # W
        "83007": "meter_pr",  #
        "83002": "inverter_ac_power",  # W
        "83009": "inverter_daily_yield",  # Wh
        "83004": "inverter_total_yield",  # Wh
        "83012": "p_radiation_h",  # W/㎡
        "83013": "daily_irradiation",  # Wh/㎡
        "83023": "plant_pr",  #
        "83005": "daily_equivalent_hours",  # h
        "83025": "plant_equivalent_hours",  # h
        "83018": "daily_yield_theoretical",  # Wh
        "83001": "inverter_ac_power_normalization",  # W/Wp
        "83008": "daily_equivalent_hours_of_inverter",  # h
        "83010": "inverter_pr",  #
        "83016": "plant_ambient_temperature",  # ℃
        "83017": "plant_module_temperature",  # ℃
        "83046": "pcs_total_active_power",  # W
        "83052": "total_load_active_power",  # W
        "83067": "total_active_power_of_pv",  # W
        "83097": "daily_direct_energy_consumption",  # Wh
        "83100": "total_direct_energy_consumption",  # Wh
        "83102": "energy_purchased_today",  # Wh
        "83105": "total_purchased_energy",  # Wh
        "83106": "load_power",  # W
        "83118": "daily_load_consumption",  # Wh
        "83124": "total_load_consumption",  # Wh
        "83119": "daily_feed_in_energy_pv",  # Wh
        "83072": "feed_in_energy_today",  # Wh
        "83075": "feed_in_energy_total",  # Wh
        "83252": "battery_level_soc",  #
        "83129": "battery_soc",  #
        "83232": "total_field_soc",  #
        "83233": "total_field_maximum_rechargeable_power",  # W
        "83234": "total_field_maximum_dischargeable_power",  # W
        "83235": "total_field_chargeable_energy",  # Wh
        "83236": "total_field_dischargeable_energy",  # Wh
        "83237": "total_field_energy_storage_maximum_reactive_power",  # var
        "83238": "total_field_energy_storage_active_power",  # W
        "83239": "total_field_reactive_power",  # var
        "83240": "total_field_power_factor",  #
        "83243": "daily_field_charge_capacity",  # Wh
        "83241": "total_field_charge_capacity",  # Wh
        "83244": "daily_field_discharge_capacity",  # Wh
        "83242": "total_field_discharge_capacity",  # Wh
        "83548": "total_number_of_charge_discharge",  #
        "83549": "grid_active_power",  # W
        "83419": "daily_highest_inverter_power_inverter_installed_capacity",  #
        "83317": "power_forecast",  # W
        "83318": "planned_es_charging_discharging_power",  # W
        "83319": "planned_es_soc",  #
        "83320": "planned_charging_power",  # Wh
        "83321": "planned_discharging_power",  # Wh
        "83322": "ess_daily_charge_ems",  # Wh
        "83324": "energy_storage_cumulative_charge",  # Wh
        "83323": "ess_daily_discharge_ems",  # Wh
        "83325": "cumulative_discharge",  # Wh
        "83327": "energy_storage_remaining_charge",  # Wh
        "83326": "energy_storage_active_power_ems",  # W
        "83328": "grid_active_power_ems",  # W
        "83329": "pv_active_power_ems",  # W
        "83330": "load_active_power_ems",  # W
        "83331": "daily_pv_yield_ems",  # Wh
        "83332": "total_pv_yield",  # Wh
        "83334": "energy_storage_soc_ems",  #
        "83335": "energy_storage_remaining_charge_ems",  # Wh
        "83743": "daily_yield_loss_load_shedding",  # Wh (daily yield loss due to load shedding)
    }
