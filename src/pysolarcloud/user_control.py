"""User-account (app/web) device parameter control via OpenAPI-shaped paths.

Clean-room implementation for sungrow-hass #271 Phase 5. Uses a normal
:class:`~pysolarcloud.user_auth.UserAuth` session (AES/RSA envelope + user token)
against the gateway paths that live probing found work:

* ``/openapi/paramSettingCheck``
* ``/openapi/paramSetting``
* ``/openapi/getParamSettingTask``

These are **not** the same as developer-OAuth paths under ``/openapi/platform/…``
(which return HTTP 401 with a user token). They also differ from
``/v1/devService/paramSetting``, which accepts the call but often returns
``result_data.code=4`` ("measuring point ID or value is empty") without a task.

Payloads and task polling match the documented OpenAPI param-setting flow used
by :class:`~pysolarcloud.control.Control` (set_type 0 write / 2 read, ``param_list``
with ``param_code`` + ``set_value``, poll until ``command_status`` is 8).

.. warning::
    Unofficial for user-account sessions. Prefer the developer OpenAPI +
    :class:`~pysolarcloud.control.Control` when a developer app is available.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from . import DeviceNotWritableError, PySolarCloudException
from .control import Control
from .user_auth import UserAuth

_LOGGER = logging.getLogger(__name__)

# Working user-token paths (live-proven on EU gateway, #271).
_PATH_CHECK = "/openapi/paramSettingCheck"
_PATH_SETTING = "/openapi/paramSetting"
_PATH_TASK = "/openapi/getParamSettingTask"

_EXPIRE_SECONDS = 120
_POLL_INTERVAL = 5
# command_status: 2 running, 8 success (OpenAPI Appendix / live observation).
_TASK_RUNNING = 2
_TASK_SUCCESS = 8


class UserControl:
    """Read/write device control parameters with a user-account session."""

    def __init__(self, auth: UserAuth) -> None:
        self.auth = auth

    async def async_check_update_support(self, device_uuid: str) -> bool:
        """Return whether the device accepts parameter updates (set_type 0)."""
        data = await self.auth.async_request(_PATH_CHECK, {"set_type": 0, "uuid": str(device_uuid)})
        result = data.get("result_data") or {}
        if str(result.get("check_result")) != "1":
            return False
        devices = result.get("dev_result_list") or []
        if not devices:
            # Some user-token responses only set top-level check_result.
            return True
        return str(devices[0].get("check_result")) == "1"

    async def async_check_read_support(self, device_uuid: str) -> bool:
        """Return whether the device accepts parameter read-back (set_type 2)."""
        data = await self.auth.async_request(_PATH_CHECK, {"set_type": 2, "uuid": str(device_uuid)})
        result = data.get("result_data") or {}
        if str(result.get("check_result")) != "1":
            return False
        devices = result.get("dev_result_list") or []
        if not devices:
            return True
        return str(devices[0].get("check_result")) == "1"

    async def async_read_parameters(
        self, device_uuid: str, param_list: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Read parameters (canonical names or raw codes) and wait for the task."""
        if param_list is None:
            codes = list(Control.config_parameters.keys())
        else:
            param_map = Control._param_code_map()
            codes = [param_map.get(p, p) for p in param_list]
        plist = [{"param_code": str(c), "set_value": ""} for c in codes]
        return await self._run_task(device_uuid, set_type=2, param_list=plist, task_prefix="Readback")

    async def async_update_parameters(self, device_uuid: str, param_values: dict[str, Any]) -> list[dict[str, Any]]:
        """Write parameters (names/codes → raw set_value strings) and wait for the task."""
        param_codes = Control._param_code_map()
        plist = [{"param_code": param_codes.get(str(p), str(p)), "set_value": str(v)} for p, v in param_values.items()]
        return await self._run_task(device_uuid, set_type=0, param_list=plist, task_prefix="Update")

    async def async_set_parameter(self, device_uuid: str, name: str, value: object) -> list[dict[str, Any]]:
        """Encode ``value`` for ``name`` via :meth:`Control.encode_parameter` and write it."""
        return await self.async_update_parameters(device_uuid, {name: Control.encode_parameter(name, value)})

    async def _run_task(
        self,
        device_uuid: str,
        *,
        set_type: int,
        param_list: list[dict[str, str]],
        task_prefix: str,
    ) -> list[dict[str, Any]]:
        body = {
            "set_type": set_type,
            "uuid": str(device_uuid),
            "task_name": f"{task_prefix} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "expire_second": _EXPIRE_SECONDS,
            "param_list": param_list,
        }
        data = await self.auth.async_request(_PATH_SETTING, body)
        # Envelope-level failure (result_code != "1") goes through ``from_response`` for
        # typed AuthError/RateLimitError classification (#64). Absent result_code stays
        # with the descriptive message so debugging isn't reduced to a raw-dict print.
        result_code = data.get("result_code")
        if result_code is not None and str(result_code) != "1":
            raise PySolarCloudException.from_response(data)
        result = data.get("result_data") or {}
        # check_result "1" = accepted; "9" = do not repeat (rate limit); others = reject.
        if str(result.get("check_result")) != "1":
            raise PySolarCloudException(
                f"paramSetting rejected for {device_uuid} (check_result={result.get('check_result')!r}): {data}"
            )
        dev_list = result.get("dev_result_list") or []
        if not dev_list:
            raise PySolarCloudException(f"paramSetting missing dev_result_list for {device_uuid}: {data}")
        device_result = dev_list[0]
        # Per-device rejection: the envelope was OK but this specific device won't accept
        # the write (permission-gated, EV charger, meter, unsupported). Raise the targeted
        # ``DeviceNotWritableError`` so consumers can silently skip the device instead of
        # retrying blindly (#63).
        device_code = str(device_result.get("code"))
        if device_code != "1" or not device_result.get("task_id"):
            _LOGGER.debug("paramSetting rejected by device %s (code=%s)", device_uuid, device_code)
            raise DeviceNotWritableError(data, device_code=device_code)
        task_id = str(device_result["task_id"])
        rows = await self._wait_for_task(device_uuid, task_id)
        # Prefer return_value on read-back; fall back to set_value (write confirmation).
        # Reuse Control's readout formatter (class attrs only; pass Control as self).
        out: list[dict[str, Any]] = []
        for param in rows:
            value = param.get("return_value")
            if value is None or value == "":
                value = param.get("set_value", "")
            out.append(Control._format_param_readout(Control, param, str(value)))  # type: ignore[arg-type]
        return out

    async def _wait_for_task(
        self, device_uuid: str, task_id: str, *, timeout: float = _EXPIRE_SECONDS
    ) -> list[dict[str, Any]]:
        deadline = asyncio.get_running_loop().time() + timeout
        await asyncio.sleep(2)
        while True:
            data = await self.auth.async_request(_PATH_TASK, {"task_id": str(task_id), "uuid": str(device_uuid)})
            result = data.get("result_data") or {}
            try:
                status = int(result.get("command_status", -1))
            except (TypeError, ValueError):
                status = -1
            _LOGGER.debug("UserControl wait_for_task %s status=%s", task_id, status)
            if status == _TASK_RUNNING:
                if asyncio.get_running_loop().time() >= deadline:
                    raise PySolarCloudException(f"Timed out waiting for task {task_id} after {timeout}s")
                await asyncio.sleep(_POLL_INTERVAL)
                continue
            if status == _TASK_SUCCESS:
                params = result.get("param_list") or []
                return list(params) if isinstance(params, list) else []
            _LOGGER.error("Task not successful %s: %s", task_id, data)
            # Envelope-level failure gets typed classification (#64); a "success"
            # envelope with a bad command_status stays as the base exception with a
            # descriptive message.
            if data.get("result_code") != "1":
                raise PySolarCloudException.from_response(data)
            raise PySolarCloudException(f"Task not successful {task_id}: {data}")
