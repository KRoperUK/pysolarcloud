import asyncio
import contextlib
from datetime import datetime
from typing import Any, cast

from . import _LOGGER, AbstractAuth, DeviceNotWritableError, PySolarCloudException

# Device-task result codes that mean "this device does not accept the write" (as opposed
# to a transient task/envelope failure). ``code`` of ``"1"`` in ``dev_result_list[0]`` is
# accepted; other codes are device-level rejections. ``"9"`` has been observed as the
# "device not supported / not writable" signal (see user_control.py's check_result "9"
# comment); we treat any non-"1" here as not-writable when the envelope itself was OK.
_DEVICE_TASK_ACCEPTED = "1"

# Server-side task lifetime (``expire_second``) used when submitting a param-setting
# task. It doubles as the default client-side deadline for :meth:`Control.wait_for_task`
# so we stop polling once the server would have expired the task anyway.
_EXPIRE_SECONDS = 120
# Seconds between polls while a task is still running.
_POLL_INTERVAL = 5


class Control:
    """Class to interact with the Grid Control API."""

    def __init__(self, auth: AbstractAuth, *, lang: str = "_en_US"):
        """Initialize the control API."""
        self.auth = auth
        self.lang = lang

    async def async_param_config_verification(self, device_uuid: str, set_type: int) -> bool:
        """Verifies whether the device supports parameter configuration."""
        uri = "/openapi/platform/paramSettingCheck"
        res = await self.auth.request(uri, {"set_type": set_type, "uuid": str(device_uuid)}, lang=self.lang)
        res.raise_for_status()
        data = await res.json()
        _LOGGER.debug("async_param_config_verification: %s", data)
        if data.get("result_code") == "1" and data["result_data"]["check_result"] == "1":
            supported = data["result_data"]["dev_result_list"][0]["check_result"]
            return bool(supported == "1")
        # Envelope failure — route through ``from_response`` so a documented result_code
        # (E00003 → AuthError, E998/E999 → RateLimitError, ...) surfaces as the right
        # typed subclass instead of a stringly-typed base exception (#64).
        raise PySolarCloudException.from_response(data)

    async def async_check_read_support(self, device_uuid: str) -> bool:
        """Check if the device supports read operations."""
        return await self.async_param_config_verification(device_uuid, 2)

    async def async_check_update_support(self, device_uuid: str) -> bool:
        """Check if the device supports read operations."""
        return await self.async_param_config_verification(device_uuid, 0)

    @staticmethod
    def _raise_for_param_response(device_uuid: str, data: dict[str, Any], *, action: str) -> None:
        """Validate a paramSetting response envelope; raise the most specific typed error.

        Three distinct failure modes come out of this one endpoint and callers historically
        collapsed them into a single stringly-typed ``PySolarCloudException``:

        1. Envelope failure (``result_code != "1"``) — route through
           :meth:`PySolarCloudException.from_response` so documented codes surface as
           :class:`AuthError` / :class:`RateLimitError` (#64). A malformed response with
           no ``result_code`` at all keeps the descriptive message so debugging isn't
           reduced to a raw-dict print.
        2. Overall ``check_result`` rejection — descriptive message; there is no
           documented API error code on this path.
        3. Per-device rejection (``dev_result_list[0]["code"] != "1"``) — raise the
           targeted :class:`DeviceNotWritableError` so consumers can silently skip a
           device that doesn't accept the write instead of retrying blindly (#63).
        """
        result_code = data.get("result_code")
        if result_code is not None and result_code != "1":
            raise PySolarCloudException.from_response(data)
        result_data = data.get("result_data") or {}
        if result_code is None:
            raise PySolarCloudException(
                f"Could not {action} parameters of device {device_uuid}: missing result_code ({data})"
            )
        if result_data.get("check_result") != "1":
            raise PySolarCloudException(
                f"Could not {action} parameters of device {device_uuid}: "
                f"check_result={result_data.get('check_result')!r} ({data})"
            )
        dev_list = result_data.get("dev_result_list") or []
        if not dev_list:
            raise PySolarCloudException(
                f"Could not {action} parameters of device {device_uuid}: response missing dev_result_list ({data})"
            )
        device_code = str(dev_list[0].get("code"))
        if device_code != _DEVICE_TASK_ACCEPTED:
            _LOGGER.debug("paramSetting rejected by device %s (code=%s) on %s", device_uuid, device_code, action)
            raise DeviceNotWritableError(data, device_code=device_code)

    async def wait_for_task(
        self, device_uuid: str, task_id: str, *, timeout: float = _EXPIRE_SECONDS
    ) -> list[dict[str, Any]]:
        """Poll for the task to be completed.

        Polls every ``_POLL_INTERVAL`` seconds while the task reports "running"
        (``command_status == 2``). Gives up after ``timeout`` seconds (defaulting to
        the same ``expire_second`` the task was submitted with) and raises
        :class:`PySolarCloudException` so a stuck task cannot hang the caller forever.
        """
        uri = "/openapi/platform/getParamSettingTask"
        params = {
            "task_id": str(task_id),
            "uuid": str(device_uuid),
        }
        deadline = asyncio.get_running_loop().time() + timeout
        await asyncio.sleep(2)
        while True:
            res = await self.auth.request(uri, params, lang=self.lang)
            res.raise_for_status()
            data = await res.json()
            _LOGGER.debug("wait_for_task: %s", data)
            if data.get("result_code") == "1" and data["result_data"]["command_status"] == 2:
                # Task is still running
                if asyncio.get_running_loop().time() >= deadline:
                    raise PySolarCloudException(f"Timed out waiting for task {task_id} to complete after {timeout}s")
                await asyncio.sleep(_POLL_INTERVAL)
                continue
            elif data.get("result_code") == "1" and data["result_data"]["command_status"] == 8:
                return cast(list[dict[str, Any]], data["result_data"]["param_list"])
            else:
                _LOGGER.error("Task not successful %s: %s", task_id, data)
                # Envelope-level failure gets typed classification (#64); a "success"
                # envelope with a bad command_status stays as the base exception with
                # a descriptive message.
                if data.get("result_code") != "1":
                    raise PySolarCloudException.from_response(data)
                raise PySolarCloudException(f"Task not succesful {task_id}: {data}")

    async def async_read_parameters(
        self, device_uuid: str, param_list: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Read the parameters from the device."""
        uri = "/openapi/platform/paramSetting"
        if param_list is None:
            ps: list[str] = list(self.config_parameters.keys())
        else:
            param_map = self._param_code_map()
            ps = [param_map.get(p, p) for p in param_list]
        _LOGGER.debug("async_read_parameters: param_list=%s", ps)
        plist = [{"param_code": p, "set_value": ""} for p in ps]
        params = {
            "set_type": 2,
            "uuid": str(device_uuid),
            "task_name": f"Readback {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "expire_second": _EXPIRE_SECONDS,
            "param_list": plist,
        }
        res = await self.auth.request(uri, params, lang=self.lang)
        res.raise_for_status()
        data = await res.json()
        _LOGGER.debug("async_read_parameters: %s", data)
        self._raise_for_param_response(device_uuid, data, action="read")
        task_id = data["result_data"]["dev_result_list"][0]["task_id"]
        results = await self.wait_for_task(device_uuid, task_id)
        return [self._format_param_readout(param, param["return_value"]) for param in results]

    @classmethod
    def _param_code_map(cls) -> dict[str, str]:
        """Map parameter names (and raw codes) to on-the-wire param_code strings.

        Prefer :attr:`config_parameters`, then fall back to :attr:`PARAMETER_SPECS` so
        parameters that are encoded-but-not-listed (historically 10003) still resolve
        by name when written via :meth:`async_update_parameters` /
        :meth:`async_set_parameter`.
        """
        codes = {v: k for k, v in cls.config_parameters.items()}
        for name, spec in cls.PARAMETER_SPECS.items():
            codes.setdefault(name, str(spec["code"]))
        return codes

    async def async_update_parameters(self, device_uuid: str, param_values: dict[str, Any]) -> list[dict[str, Any]]:
        """Update parameters to the device."""
        uri = "/openapi/platform/paramSetting"
        param_codes = self._param_code_map()
        plist = [{"param_code": param_codes.get(str(p), str(p)), "set_value": str(v)} for p, v in param_values.items()]
        _LOGGER.debug("async_update_parameters: param_values=%s", plist)
        params = {
            "set_type": 0,
            "uuid": str(device_uuid),
            "task_name": f"Update {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "expire_second": _EXPIRE_SECONDS,
            "param_list": plist,
        }
        res = await self.auth.request(uri, params, lang=self.lang)
        res.raise_for_status()
        data = await res.json()
        _LOGGER.debug("async_update_parameters: %s", data)
        self._raise_for_param_response(device_uuid, data, action="update")
        task_id = data["result_data"]["dev_result_list"][0]["task_id"]
        results = await self.wait_for_task(device_uuid, task_id)
        return [self._format_param_readout(param, param["set_value"]) for param in results]

    async def async_heartbeat(self, device_uuid: str, interval_seconds: int) -> None:
        """Send a single External EMS heartbeat (param 10017) and return.

        The iSolarCloud API expects the heartbeat value to be the polling interval itself
        (1-1000 seconds, see Appendix 10 of the developer portal). When the EMS stops sending
        heartbeats the inverter reverts to its default mode.

        For a long-running heartbeat use :meth:`heartbeat_loop` instead.
        """
        if not 1 <= interval_seconds <= 1000:
            raise ValueError("heartbeat interval must be between 1 and 1000 seconds")
        await self.async_update_parameters(device_uuid, {"external_ems_heartbeat": str(interval_seconds)})

    async def heartbeat_loop(self, device_uuid: str, interval_seconds: int, stop_event: asyncio.Event) -> None:
        """Continuously send External EMS heartbeats until *stop_event* is set.

        Each heartbeat refreshes param 10017 to *interval_seconds*. Sleeps
        ``interval_seconds`` between heartbeats so the inverter never times out.
        """
        if not 1 <= interval_seconds <= 1000:
            raise ValueError("heartbeat interval must be between 1 and 1000 seconds")
        while not stop_event.is_set():
            try:
                await self.async_heartbeat(device_uuid, interval_seconds)
            except PySolarCloudException as err:
                # Expected API/task failures: log and keep the loop alive.
                _LOGGER.warning("EMS heartbeat failed for %s: %s", device_uuid, err)
            except Exception as err:  # pylint: disable=broad-except
                # Unexpected errors (malformed responses, network edge cases) used to kill
                # the loop silently; keep retrying so dispatch mode does not drop mid-session.
                _LOGGER.exception("EMS heartbeat unexpected error for %s: %s", device_uuid, err)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
            return

    def _format_param_readout(self, param: dict[str, Any], value: str) -> dict[str, Any]:
        """Format the parameter response."""
        readout = {
            "id": param["param_code"],
            "code": self.config_parameters.get(param["param_code"], param["param_code"]),
            "name": param["point_name"],
            "value": value,
            "unit": param.get("unit", ""),
            "precision": param.get("set_precision"),
        }
        if param.get("set_val_name"):
            value_set_names = param["set_val_name"].split("|")
            value_set_values = param["set_val_name_val"].split("|")
            if value in value_set_values:
                readout["value"] = value_set_names[value_set_values.index(value)]
                readout["value_set"] = dict(zip(value_set_names, value_set_values, strict=False))
        else:
            with contextlib.suppress(ValueError):
                readout["value"] = float(value)
        return readout

    # Canonical name -> on-the-wire value for the `charge_discharge_command` parameter
    # (10004). The API returns either the numeric value or one of these names depending
    # on firmware.
    CHARGE_DISCHARGE_COMMANDS = {
        "stop": "204",
        "charge": "170",
        "discharge": "187",
    }

    # Canonical name -> on-the-wire value for `forced_charging` (10065).
    FORCED_CHARGING = {
        "disable": "85",
        "enable": "170",
    }

    config_parameters = {
        "10001": "soc_upper_limit",
        "10002": "soc_lower_limit",
        "10004": "charge_discharge_command",
        "10005": "charge_discharge_power",
        "10007": "limited_power_switch",
        "10008": "active_power_limit_ratio",
        "10009": "reactive_power_regulation_mode",
        "10010": "q_t",
        "10011": "power_on",
        "10012": "feed_in_limitation",
        "10013": "feed_in_limitation_value",
        "10014": "feed_in_limitation_ratio",
        "10017": "external_ems_heartbeat",
        "10024": "battery_first",
        "10025": "active_power_soft_start_after_fault",
        "10026": "active_power_soft_start_time_after_fault",
        "10027": "active_power_soft_start",
        "10028": "active_power_soft_start_gradient",
        "10029": "active_power_gradient_control",
        "10030": "active_power_decline_gradient",
        "10031": "active_power_rising_gradient",
        "10032": "active_power_setting_persistence",
        "10033": "shutdown_when_active_power_limit_to_0",
        "10034": "reactive_response",
        "10035": "reactive_power_regulation_time",
        "10036": "pf",
        "10065": "forced_charging",
        "10066": "forced_charging_valid_time",
        "10067": "forced_charging_start_time_1_hour",
        "10068": "forced_charging_start_time_1_minute",
        "10069": "forced_charging_end_time_1_hour",
        "10070": "forced_charging_end_time_1_minute",
        "10071": "forced_charging_target_soc_1",
        "10072": "forced_charging_start_time_2_hour",
        "10073": "forced_charging_start_time_2_minute",
        "10074": "forced_charging_end_time_2_hour",
        "10075": "forced_charging_end_time_2_minute",
        "10076": "forced_charging_target_soc_2",
        # 10091 max_charging_power / 10092 max_discharging_power are display×100 per Appendix 10,
        # but their safe device limits are unknown, so encode_parameter would emit an unscaled,
        # unbounded value. Left out until we can read the per-device limits (see #16,
        # getDevPropertyPointValue) and add PARAMETER_SPECS with a correct scale/min/max.
        # "10091": "max_charging_power",
        # "10092": "max_discharging_power",
        # Energy Management Mode (Appendix 10). Required for charge/discharge to take
        # effect: writing 10004/10005 alone leaves the plant in Self-consumption and the
        # inverter ignores the command (see sungrow-hass #231). Values: 0 self-consumption,
        # 2 compulsory/forced, 3 external energy dispatch, 4 VPP. Previously omitted after
        # early bulk-read validation failures; named writes are resolved via PARAMETER_SPECS
        # even if a given device rejects a bulk read of this code.
        "10003": "energy_management_mode",
        # These are defined in API documentation but are rejected by the API as duplicates of 10071 and 10076
        # "10015": "forced_charging_target_soc1",
        # "10016": "forced_charging_target_soc2",
        # These are defined in API documentation but cause validation error from the API
        # "10006": "existing_inverter",
        # "10082": "charge_discharge_command_in_external_dispatch_mode",
        # "10083": "charging_discharging_power_in_external_dispatch_mode",
        # "10084": "power_limiting_command_in_external_dispatch_mode",
        # "10085": "ems_heartbeat_settings_in_external_dispatch_mode",
        # "10086": "energy_management_mode",
        # "10087": "feed_in_limitation_ratio_in_external_dispatch_mode",
        # "10088": "feed_in_limitation_on_off_in_external_dispatch_mode",
        # "10089": "feed_in_limitation_value_in_external_dispatch_mode",
    }

    # Value encodings for the settable control parameters, from Appendix 10 (Control
    # Parameter Definitions) of the iSolarCloud OpenAPI documentation. ``async_update_parameters``
    # sends values verbatim, so a caller must send the *raw* value the device expects.
    # These specs capture how a human-friendly value maps to that raw value:
    #   - ``scale``: multiply the display value (watts, percent, seconds) to get the raw
    #     integer, e.g. SOC/ratios are tenths of a percent (700-1000 = 70-100%, scale 10),
    #     power is in watts (scale 1);
    #   - ``values``: for enum parameters, an option name -> raw code map.
    # Use :meth:`encode_parameter` / :meth:`async_set_parameter` to apply them.
    PARAMETER_SPECS: dict[str, dict[str, Any]] = {
        "soc_upper_limit": {"code": "10001", "kind": "percent", "unit": "%", "scale": 10, "min": 70, "max": 100},
        "soc_lower_limit": {"code": "10002", "kind": "percent", "unit": "%", "scale": 10, "min": 0, "max": 50},
        "energy_management_mode": {
            "code": "10003",
            "kind": "enum",
            "values": {"self_consumption": "0", "compulsory": "2", "external_dispatch": "3", "vpp": "4"},
        },
        "charge_discharge_command": {
            "code": "10004",
            "kind": "enum",
            "values": {"charge": "170", "discharge": "187", "stop": "204"},
        },
        "charge_discharge_power": {"code": "10005", "kind": "power", "unit": "W", "scale": 1, "min": 0, "max": 5000},
        "limited_power_switch": {"code": "10007", "kind": "enum", "values": {"enable": "170", "disable": "85"}},
        "active_power_limit_ratio": {
            "code": "10008",
            "kind": "percent",
            "unit": "%",
            "scale": 10,
            "min": 0,
            "max": 100,
        },
        "feed_in_limitation": {"code": "10012", "kind": "enum", "values": {"enable": "170", "disable": "85"}},
        "feed_in_limitation_value": {"code": "10013", "kind": "power", "unit": "W", "scale": 1, "min": 0, "max": None},
        "feed_in_limitation_ratio": {
            "code": "10014",
            "kind": "percent",
            "unit": "%",
            "scale": 10,
            "min": 0,
            "max": 100,
        },
        "external_ems_heartbeat": {"code": "10017", "kind": "duration", "unit": "s", "scale": 1, "min": 1, "max": 1000},
        "battery_first": {"code": "10024", "kind": "enum", "values": {"enable": "170", "disable": "85"}},
        "forced_charging": {"code": "10065", "kind": "enum", "values": {"enable": "170", "disable": "85"}},
        "forced_charging_target_soc_1": {
            "code": "10071",
            "kind": "percent",
            "unit": "%",
            "scale": 1,
            "min": 0,
            "max": 100,
        },
        "forced_charging_target_soc_2": {
            "code": "10076",
            "kind": "percent",
            "unit": "%",
            "scale": 1,
            "min": 0,
            "max": 100,
        },
        # Reactive-power / power-factor control (Appendix 10). The mode select gates the
        # others: Q(t) needs mode Q(t); PF needs mode PF; reactive response/time need any
        # non-OFF mode. Encodings are unambiguous, unlike 10091/10092 (see above).
        "reactive_power_regulation_mode": {
            "code": "10009",
            "kind": "enum",
            "values": {"off": "85", "pf": "161", "q_t": "162", "q_p": "163", "q_u": "164"},
        },
        "q_t": {"code": "10010", "kind": "percent", "unit": "%", "scale": 10, "min": -60, "max": 60},
        "reactive_response": {"code": "10034", "kind": "enum", "values": {"enable": "170", "disable": "85"}},
        "reactive_power_regulation_time": {
            "code": "10035",
            "kind": "duration",
            "unit": "s",
            "scale": 10,
            "min": 0.1,
            "max": 600,
        },
        "pf": {"code": "10036", "kind": "ratio", "unit": "", "scale": 1000, "min": -1, "max": 1},
    }

    @classmethod
    def encode_parameter(cls, name: str, value: object) -> str:
        """Encode a human-friendly value into the raw string the API expects.

        For enum parameters ``value`` is an option name (e.g. ``"charge"``) or an
        already-raw code. For numeric parameters ``value`` is in the parameter's
        display unit (watts, percent, seconds) and is scaled to the integer the API
        expects (see :attr:`PARAMETER_SPECS`). Parameters without a spec are returned
        as ``str(value)`` unchanged.

        Raises:
            ValueError: for an unknown enum option, a non-numeric numeric value, or a
                numeric value outside the parameter's declared ``min``/``max`` bounds.
        """
        spec = cls.PARAMETER_SPECS.get(name)
        if spec is None:
            return str(value)
        if spec["kind"] == "enum":
            values: dict[str, str] = spec["values"]
            key = str(value).lower()
            if key in values:
                return values[key]
            if str(value) in values.values():
                return str(value)
            raise ValueError(f"Unknown option {value!r} for {name}; expected one of {sorted(values)}")
        try:
            numeric = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as err:
            raise ValueError(f"{name} expects a numeric value, got {value!r}") from err
        # Enforce the declared display-unit bounds before scaling so an out-of-range
        # value is never sent to hardware (#13). Bounds may be omitted or None (open-ended).
        low = spec.get("min")
        high = spec.get("max")
        if (low is not None and numeric < low) or (high is not None and numeric > high):
            unit = spec.get("unit", "")
            low_str = "-inf" if low is None else f"{low}{unit}"
            high_str = "inf" if high is None else f"{high}{unit}"
            raise ValueError(f"{name} value {value!r} out of range [{low_str}, {high_str}]")
        return str(int(round(numeric * spec.get("scale", 1))))

    async def async_set_parameter(self, device_uuid: str, name: str, value: object) -> list[dict[str, Any]]:
        """Encode ``value`` for ``name`` (see :meth:`encode_parameter`) and write it."""
        return await self.async_update_parameters(device_uuid, {name: self.encode_parameter(name, value)})
