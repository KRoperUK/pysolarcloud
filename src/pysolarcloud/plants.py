from datetime import datetime, timedelta
from enum import Enum
from . import AbstractAuth, PySolarCloudException, _LOGGER

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
    ENERGY_STORAGE_SYSTEM_2 = 37
    BATTERY = 43
    BATTERY_CLUSTER_MANAGEMENT_UNIT = 44
    LOCAL_CONTROLLER = 45
    BATTERY_SYSTEM_CONTROLLER = 52


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

    async def async_get_plants(self) -> list[dict]:
        """Return the list of plants accessible to the user."""
        uri = "/openapi/platform/queryPowerStationList"
        res = await self.auth.request(uri, {"page": 1, "size": 100})
        res.raise_for_status()
        data = await res.json()
        if "error" in data:
            _LOGGER.error("Error response from %s: %s", uri, data)
            raise PySolarCloudException(res)
        plants = [plant for plant in data["result_data"]["pageList"]]
        _LOGGER.debug("async_get_plants: %s", plants)
        return plants

    async def async_get_plant_details(self, plant_id: str | list[str]) -> list[dict]:
        """Return details about one or more plants."""
        if isinstance(plant_id, list):
            ps = ",".join(plant_id)
        else:
            ps = plant_id
        uri = "/openapi/platform/getPowerStationDetail"
        res = await self.auth.request(uri, {"ps_ids": ps})
        res.raise_for_status()
        data = await res.json()
        if "error" in data:
            _LOGGER.error("Error response from %s: %s", uri, res)
            raise PySolarCloudException(res)
        plants = data["result_data"]["data_list"]
        _LOGGER.debug("async_get_plant_details: %s", plants)
        return plants

    async def async_get_plant_devices(self, plant_id: str, *, device_types: list[DeviceType | int] = []) -> list[dict]:
        """Return details about the devices for a plant."""
        uri = "/openapi/platform/getDeviceListByPsId"
        params = {"ps_id": plant_id, "page": 1, "size": 100}
        if device_types:
            params["device_type_list"] = [str(d.value) if isinstance(d, DeviceType) else str(d) for d in device_types]
        res = await self.auth.request(uri, params)
        res.raise_for_status()
        data = await res.json()
        if "error" in data:
            _LOGGER.error("Error response from %s: %s", uri, res)
            raise PySolarCloudException(res)
        devices = data["result_data"]["pageList"]
        for device in devices:
            # Convert the device type and fault status to enums
            if device["device_type"] in DeviceType:
                device["device_type"] = DeviceType(device["device_type"])
            if device["dev_fault_status"] in DeviceFaultStaus:
                device["dev_fault_status"] = DeviceFaultStaus(device["dev_fault_status"])
        _LOGGER.debug("async_get_plant_devices: %s", devices)
        return devices

    async def async_get_realtime_data(self, plant_id: str | list[str], *, measure_points=None) -> dict:
        """Return the latest realtime data from one or more plants.
        
        plant_id: str | list[str] - The ID of the plant or a list of plant IDs.
        measure_points: list[str] - A list of measure points to return. If None, all measure points are returned.
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
        if isinstance(plant_id, list):
            ps = plant_id
        else:
            ps = [plant_id]
        if measure_points is None:
            ms = list(self.measure_points.keys())
        else:
            measure_points_map = {v: k for k, v in self.measure_points.items()}
            ms = [m if m.isdigit() else measure_points_map[m] for m in measure_points]
        uri = "/openapi/platform/getPowerStationRealTimeData"
        res = await self.auth.request(uri, {"ps_id_list": ps, "point_id_list": ms, "is_get_point_dict": "1"}, lang=self.lang)
        res = await res.json()
        if "error" in res:
            _LOGGER.error("Error response from %s: %s", uri, res)
            raise PySolarCloudException(res)
        point_dict = dict([(str(point["point_id"]), point) for point in res["result_data"]["point_dict"]])
        plants = {}
        for plant in res["result_data"]["device_point_list"]:
            data = [self._format_measure_point(k[1:], v, point_dict) for k,v in plant.items() if k[0]=='p' and k[1:].isdigit()]
            data_as_dict = {d["code"]: d for d in data}
            plants[str(plant["ps_id"])] = data_as_dict
        _LOGGER.debug("async_get_realtime_data: %s", plants)
        return plants

    async def async_get_realtime_device_data(self, device_type: DeviceType | int, ps_key: str | list[str], *, measure_points=None) -> dict:
        """Return the latest realtime data from one or more devices.
        
        device_type: DeviceType - The type of device to query.
        ps_key: str | list[str] - The key of the device or a list of device keys.
        measure_points: list[str] - A list of measure points to return. If None, all measure points are returned.
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
        if isinstance(ps_key, list):
            ps = ps_key
        else:
            ps = [ps_key]
        if measure_points is None:
            ms = list(self.measure_points.keys())
        else:
            measure_points_map = {v: k for k, v in self.measure_points.items()}
            ms = [m if m.isdigit() else measure_points_map[m] for m in measure_points]
        if isinstance(device_type, DeviceType):
            dt = device_type.value
        else:
            dt = device_type
        uri = "/openapi/platform/getDeviceRealTimeData"
        res = await self.auth.request(uri, {"ps_key_list": ps, "device_type": dt, "point_id_list": ms[:35], "is_get_point_dict": "1"}, lang=self.lang)
        res = await res.json()
        if "error" in res:
            _LOGGER.error("Error response from %s: %s", uri, res)
            raise PySolarCloudException(res)
        point_dict = dict([(str(point["point_id"]), point) for point in res["result_data"]["point_dict"]])
        devices = {}
        for dp in res["result_data"]["device_point_list"]:
            dev = dp["device_point"]
            data = [self._format_measure_point(k[1:], v, point_dict) for k,v in dev.items() if k[0]=='p' and k[1:].isdigit()]
            data_as_dict = {d["code"]: d for d in data}
            devices[str(dev["ps_key"])] = data_as_dict
        _LOGGER.debug("async_get_realtime_device_data: %s", devices)
        return devices

    async def async_get_historical_data(self, plant_id: str | list[str], start_time: datetime, end_time: datetime = None, *, measure_points=None, interval=timedelta(minutes=60)) -> dict:
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
        if isinstance(plant_id, list):
            ps = str(plant_id)
        else:
            ps = [plant_id]
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
            "points": ",".join(["p"+m for m in ms]), 
            "is_get_point_dict": "1",
            "start_time_stamp": start_time.strftime(TS_FORMAT),
            "end_time_stamp": end_time.strftime(TS_FORMAT),
            "minute_interval": str(interval.seconds // 60),
        }
        res = await self.auth.request(uri, params, lang=self.lang)
        res = await res.json()
        if res.get("result_code") != "1":
            _LOGGER.error("Error response from %s: %s", uri, res)
            raise PySolarCloudException(res)
        point_dict = dict([(str(point["point_id"]), point) for point in res["result_data"]["point_dict"]])
        plants = {}
        for plant_id, plant in res["result_data"].items():
            if plant_id == "point_dict":
                continue
            series = []
            for frame in plant:
                data = {}
                ts = datetime.strptime(frame["time_stamp"], TS_FORMAT)
                for k,v in frame.items():
                    if k == "time_stamp":
                        continue
                    else:
                        data = self._format_measure_point(k[1:], v, point_dict)
                        data["timestamp"] = ts
                    series.append(data)
            plants[str(plant_id)] = series
        _LOGGER.debug("async_get_historical_data: %s", plants)
        return plants
    
    def _format_measure_point(self, point_id: str, point_value: str, point_dict: dict) -> dict:
        try:
            v = float(point_value) if point_value is not None else None
        except ValueError:
            v = point_value
        return {
            "id": point_id,
            "code": self.measure_points.get(point_id, point_id),
            "value": v,
            "unit": point_dict.get(point_id, {}).get("point_unit", None),
            "name": point_dict.get(point_id, {}).get("point_name", None),
        }

    measure_points = {
        "83022": "daily_yield", # Wh
        "83024": "total_yield", # Wh
        "83033": "power", # W
        "83019": "power_fraction", # Plant Power/Installed Power of Plant 
        "83006": "meter_daily_yield", # Wh
        "83020": "meter_total_yield", # Wh
        "83011": "meter_e_daily_consumption", # Wh
        "83021": "accumulative_power_consumption_by_meter", # Wh
        "83032": "meter_ac_power", # W
        "83007": "meter_pr", # 
        "83002": "inverter_ac_power", # W
        "83009": "inverter_daily_yield", # Wh
        "83004": "inverter_total_yield", # Wh
        "83012": "p_radiation_h", # W/㎡
        "83013": "daily_irradiation", # Wh/㎡
        "83023": "plant_pr", # 
        "83005": "daily_equivalent_hours", # h
        "83025": "plant_equivalent_hours", # h
        "83018": "daily_yield_theoretical", # Wh
        "83001": "inverter_ac_power_normalization", # W/Wp
        "83008": "daily_equivalent_hours_of_inverter", # h
        "83010": "inverter_pr", # 
        "83016": "plant_ambient_temperature", # ℃
        "83017": "plant_module_temperature", # ℃
        "83046": "pcs_total_active_power", # W
        "83052": "total_load_active_power", # W
        "83067": "total_active_power_of_pv", # W
        "83097": "daily_direct_energy_consumption", # Wh
        "83100": "total_direct_energy_consumption", # Wh
        "83102": "energy_purchased_today", # Wh
        "83105": "total_purchased_energy", # Wh
        "83106": "load_power", # W
        "83118": "daily_load_consumption", # Wh
        "83124": "total_load_consumption", # Wh
        "83119": "daily_feed_in_energy_pv", # Wh
        "83072": "feed_in_energy_today", # Wh
        "83075": "feed_in_energy_total", # Wh
        "83252": "battery_level_soc", # 
        "83129": "battery_soc", # 
        "83232": "total_field_soc", # 
        "83233": "total_field_maximum_rechargeable_power", # W
        "83234": "total_field_maximum_dischargeable_power", # W
        "83235": "total_field_chargeable_energy", # Wh
        "83236": "total_field_dischargeable_energy", # Wh
        "83237": "total_field_energy_storage_maximum_reactive_power", # W
        "83238": "total_field_energy_storage_active_power", # W
        "83239": "total_field_reactive_power", # var
        "83240": "total_field_power_factor", # 
        "83243": "daily_field_charge_capacity", # Wh
        "83241": "total_field_charge_capacity", # Wh
        "83244": "daily_field_discharge_capacity", # Wh
        "83242": "total_field_discharge_capacity", # Wh
        "83548": "total_number_of_charge_discharge", # 
        "83549": "grid_active_power", # W
        "83419": "daily_highest_inverter_power_inverter_installed_capacity", # 
        "83317": "power_forecast", # W
        "83318": "planned_es_charging_discharging_power", # W
        "83319": "planned_es_soc", # 
        "83320": "planned_charging_power", # Wh
        "83321": "planned_discharging_power", # Wh
        "83322": "ess_daily_charge_ems", # Wh
        "83324": "energy_storage_cumulative_charge", # Wh
        "83323": "ess_daily_discharge_ems", # Wh
        "83325": "cumulative_discharge", # Wh
        "83327": "energy_storage_remaining_charge", # Wh
        "83326": "energy_storage_active_power_ems", # W
        "83328": "grid_active_power_ems", # W
        "83329": "pv_active_power_ems", # W
        "83330": "load_active_power_ems", # W
        "83331": "daily_pv_yield_ems", # Wh
        "83332": "total_pv_yield", # Wh
        "83334": "energy_storage_soc_ems", # 
        "83335": "energy_storage_remaining_charge_ems", # Wh
        "58601": "battery_voltage", # V
        "58602": "battery_current", # A
        "58603": "battery_temperature", # °C
        "58604": "battery_level",
        "58605": "battery_health_soh",
        "58606": "total_battery_charging_energy", # Wh
        "58607": "total_battery_discharging_energy", # Wh
        "58608": "battery_operation_status",
        "58609": "standard_health_status",
        "58610": "max_cell_voltage", # mV
        "58611": "position_of_max_cell_voltage",
        "58612": "min_cell_voltage", # mV
        "58613": "position_of_min_cell_voltage",
        "58614": "max_module_temperature", # °C
        "58615": "position_of_max_module_temperature",
        "58616": "min_module_temperature", # °C
        "58617": "position_of_min_module_temperature",
        "58618": "max_cell_voltage_module_1", # mV
        "58619": "max_cell_voltage_module_2", # mV
        "58620": "max_cell_voltage_module_3", # mV
        "58621": "max_cell_voltage_module_4", # mV
        "58622": "max_cell_voltage_module_5", # mV
        "58623": "max_cell_voltage_module_6", # mV
        "58624": "max_cell_voltage_module_7", # mV
        "58625": "max_cell_voltage_module_8", # mV
        "58626": "min_cell_voltage_module_1", # mV
        "58627": "min_cell_voltage_module_2", # mV
        "58628": "min_cell_voltage_module_3", # mV
        "58629": "min_cell_voltage_module_4", # mV
        "58630": "min_cell_voltage_module_5", # mV
        "58631": "min_cell_voltage_module_6", # mV
        "58632": "min_cell_voltage_module_7", # mV
        "58633": "min_cell_voltage_module_8", # mV
        "58635": "dc_contactor_status",
        "58636": "fault_module_id",
        "13011": "active_power", # W
        "13003": "total_dc_power", # W
        "13157": "phase_a_voltage", # V
        "13158": "phase_b_voltage", # V
        "13159": "phase_c_voltage", # V
        "13008": "phase_a_current", # A
        "13009": "phase_b_current", # A
        "13010": "phase_c_current", # A
        "13012": "total_reactive_power", # var
        "13160": "array_insulation_resistance", # kΩ
        "13007": "grid_frequency", # Hz
        "18065": "phase_a_backup_power", # W
        "18066": "phase_b_backup_power", # W
        "18067": "phase_c_backup_power", # W
        "18068": "total_backup_power", # W
        "18062": "phase_a_backup_current", # A
        "18063": "phase_b_backup_current", # A
        "18064": "phase_c_backup_current", # A
        "13020": "total_operation_time", # H
        "13134": "total_pv_yield", # Wh
        "13112": "daily_pv_yield", # Wh
        "13187": "ac_voltage", # V
        "13188": "ac_current", # A
        "13004": "a_b_line_voltage", # V
        "13005": "b_c_line_voltage", # V
        "13006": "c_a_line_voltage", # V
        "13019": "internal_air_temperature", # °C
        "13161": "bus_voltage", # V
        "13013": "total_power_factor",
        "13001": "mppt1_voltage", # V
        "13002": "mppt1_current", # A
        "13105": "mppt2_voltage", # V
        "13106": "mppt2_current", # A
        "13107": "mppt3_voltage", # V
        "13108": "mppt3_current", # A
        "13109": "mppt4_voltage", # V
        "13110": "mppt4_current", # A
        "13122": "feed_in_energy_today", # Wh
        "13125": "total_feed_in_energy", # Wh
        "13147": "energy_purchased_today", # Wh
        "13148": "total_purchased_energy", # Wh
        "13149": "purchased_power", # W
        "13121": "feed_in_power", # W
        "13173": "feed_in_energy_today_pv", # Wh
        "13175": "total_feed_in_energy_pv", # Wh
        "13141": "battery_level", # SOC
        "13029": "daily_battery_discharging_energy", # Wh
        "13028": "daily_battery_charging_energy", # Wh
        "13138": "battery_voltage", # V
        "13139": "battery_current", # A
        "13035": "total_battery_discharging_energy", # Wh
        "13034": "total_battery_charging_energy", # Wh
        "13142": "battery_health_soh",
        "13143": "battery_temperature", # °C
        "13162": "max_charging_current_bms", # A
        "13163": "max_discharging_current_bms", # A
        "13174": "daily_battery_charging_energy_from_pv", # Wh
        "13176": "total_battery_charging_energy_from_pv", # Wh
        "13126": "battery_charging_power", # W
        "13150": "battery_discharging_power", # W
        "13199": "daily_load_consumption", # Wh
        "13137": "total_load_energy_consumption_from_pv", # Wh
        "13119": "load_power", # W
        "13130": "total_load_consumption", # Wh
        "13116": "daily_direct_energy_consumption", # Wh
        "13144": "daily_self_consumption_rate",
        "13016": "total_charging_time", # H
        "13017": "total_discharging_time", # H
        "13018": "total_apparent_power", # VA
        "13023": "daily_charging_time", # H
        "13024": "daily_discharging_time", # H
        "13118": "annual_direct_energy_consumption", # Wh
        "13165": "mdsp_off_grid_start_up_status",
        "13166": "sdsp_working_mode",
        "13167": "sdsp_off_grid_start_status",
        "13168": "di_status",
        "13169": "battery_voltage_bms", # V
        "13170": "battery_soc_bms",
        "13171": "ems_status",
        "13172": "daily_self_sufficiency_rate",
        "13140": "battery_capacity_kwh", # Wh
        "18075": "channel_2_total_apparent_power", # VA
        "18076": "channel_2_phase_a_apparent_power", # VA
        "18077": "channel_2_phase_b_apparent_power", # VA
        "18078": "channel_2_phase_c_apparent_power", # VA
        "18079": "channel_2_total_active_power", # W
        "18080": "channel_2_phase_a_active_power", # W
        "18081": "channel_2_phase_b_active_power", # W
        "18082": "channel_2_phase_c_active_power", # W
        "18083": "channel_2_total_reactive_power", # var
        "18084": "channel_2_phase_a_reactive_power", # var
        "18085": "channel_2_phase_b_reactive_power", # var
        "18086": "channel_2_phase_c_reactive_power", # var
        "18087": "channel_2_power_factor",
        "18088": "channel_2_total_purchased_energy", # Wh
        "18089": "channel_2_phase_a_purchased_energy", # Wh
        "18090": "channel_2_phase_b_purchased_energy", # Wh
        "18091": "channel_2_phase_c_purchased_energy", # Wh
        "18092": "channel_2_total_feed_in_energy", # Wh
        "18093": "channel_2_phase_a_feed_in_energy", # Wh
        "18094": "channel_2_phase_b_feed_in_energy", # Wh
        "18095": "channel_2_phase_c_feed_in_energy", # Wh
        "18103": "phase_a_backup_voltage", # V
        "18104": "phase_b_backup_voltage", # V
        "18105": "phase_c_backup_voltage", # V
        "18108": "meter_phase_a_voltage", # V
        "18109": "meter_phase_b_voltage", # V
        "18110": "meter_phase_c_voltage", # V
        "13146": "operating_status",
    }