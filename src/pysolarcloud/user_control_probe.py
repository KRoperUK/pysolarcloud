"""Experimental probes for user-account EMS control endpoints (sungrow-hass #271).

**Not a stable public API.** Used by live/unit tests to answer whether a plain
iSolarCloud user session can read/write control parameters (Phase 5 spike).

Clean-room only. Live finding: user tokens work on ``/openapi/paramSetting*``
(no ``/platform`` segment). Developer paths under ``/openapi/platform/…`` return
401; ``/v1/devService/paramSetting`` often returns logical ``code=4``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .user_auth import UserAuth

_LOGGER = logging.getLogger(__name__)

# Prefer power-on / common inverter params for probes — EMS mode 10003 is often
# unavailable on PV-only plants (template code 6).
_PROBE_PARAM_CODE = "10011"  # Startup/shutdown (live return_value observed)
_EXPIRE_SECONDS = 120

WRITE_OK_ENV = "SUNGROW_USER_WRITE_OK"

# Working user-token control surface (live-proven).
WORKING_CHECK_PATH = "/openapi/paramSettingCheck"
WORKING_SETTING_PATH = "/openapi/paramSetting"
WORKING_TASK_PATH = "/openapi/getParamSettingTask"


@dataclass(frozen=True)
class ProbeResult:
    """One endpoint probe outcome (safe to log: no secrets)."""

    label: str
    path: str
    result_code: str | None
    result_msg: str | None
    ok: bool
    detail: str
    raw_result_data: Any = None


BodyBuilder = Callable[[str], dict[str, Any]]


def _task_name(prefix: str) -> str:
    return f"{prefix} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"


def _body_param_check(set_type: int) -> BodyBuilder:
    def build(device_uuid: str) -> dict[str, Any]:
        return {"set_type": set_type, "uuid": str(device_uuid)}

    return build


def _body_param_setting_read(param_code: str = _PROBE_PARAM_CODE) -> BodyBuilder:
    def build(device_uuid: str) -> dict[str, Any]:
        return {
            "set_type": 2,
            "uuid": str(device_uuid),
            "task_name": _task_name("Readback"),
            "expire_second": _EXPIRE_SECONDS,
            "param_list": [{"param_code": param_code, "set_value": ""}],
        }

    return build


def _body_param_setting_write(param_code: str, set_value: str) -> BodyBuilder:
    def build(device_uuid: str) -> dict[str, Any]:
        return {
            "set_type": 0,
            "uuid": str(device_uuid),
            "task_name": _task_name("Update"),
            "expire_second": _EXPIRE_SECONDS,
            "param_list": [{"param_code": param_code, "set_value": str(set_value)}],
        }

    return build


# Candidates ordered: proven working surface first, then negatives / dead ends.
READ_CANDIDATES: list[tuple[str, str, BodyBuilder]] = [
    ("openapi_paramSettingCheck_read", WORKING_CHECK_PATH, _body_param_check(2)),
    ("openapi_paramSettingCheck_update", WORKING_CHECK_PATH, _body_param_check(0)),
    ("openapi_paramSetting_read", WORKING_SETTING_PATH, _body_param_setting_read()),
    # Developer OAuth paths — expected 401 with a user token.
    ("platform_paramSettingCheck_read", "/openapi/platform/paramSettingCheck", _body_param_check(2)),
    ("platform_paramSetting_read", "/openapi/platform/paramSetting", _body_param_setting_read()),
    # App-style /v1 — check may pass; task often fails with code 4.
    ("v1_dev_paramSettingCheck_read", "/v1/devService/paramSettingCheck", _body_param_check(2)),
    ("v1_dev_paramSetting_read", "/v1/devService/paramSetting", _body_param_setting_read()),
]


def write_ok_enabled() -> bool:
    """True when the optional live write gate is set."""
    return os.getenv(WRITE_OK_ENV, "").strip() in {"1", "true", "TRUE", "yes", "YES"}


def _envelope_ok(data: dict[str, Any]) -> bool:
    return data.get("result_msg") == "success" or str(data.get("result_code")) == "1"


def _task_accepted(result: dict[str, Any]) -> bool:
    """True when paramSetting accepted a task (dev_result_list entry with code 1 + task_id)."""
    if result.get("task_id") and str(result.get("code", "1")) in {"1", "success"}:
        return True
    dev_list = result.get("dev_result_list")
    if not isinstance(dev_list, list) or not dev_list:
        return False
    first = dev_list[0] if isinstance(dev_list[0], dict) else {}
    return bool(first.get("task_id")) and str(first.get("code")) == "1"


def _control_logical_ok(path: str, data: dict[str, Any]) -> bool:
    """True when a control probe did useful work — not just an outer success shell."""
    if not _envelope_ok(data):
        return False
    result = data.get("result_data")
    path = path.rstrip("/")
    if path.endswith("paramSettingCheck"):
        if not isinstance(result, dict):
            return False
        if str(result.get("check_result")) != "1":
            return False
        # Prefer nested device check when present (OpenAPI shape).
        devices = result.get("dev_result_list")
        if isinstance(devices, list) and devices:
            first = devices[0] if isinstance(devices[0], dict) else {}
            return str(first.get("check_result", "1")) == "1"
        return True
    if path.endswith("paramSetting") or path.endswith("setDeviceParam"):
        if not isinstance(result, dict):
            return False
        # Bare {code: "4"} / {code: "6"} without task_id is a logical failure.
        if "code" in result and not result.get("task_id") and not result.get("dev_result_list"):
            return str(result.get("code")) in {"1", "success"}
        return _task_accepted(result)
    return True


def _summarize_envelope(path: str, data: dict[str, Any]) -> tuple[str | None, str | None, bool, str]:
    code = data.get("result_code")
    msg = data.get("result_msg")
    code_s = None if code is None else str(code)
    msg_s = None if msg is None else str(msg)
    ok = _control_logical_ok(path, data)
    result = data.get("result_data")
    detail_bits = [f"code={code_s}", f"msg={msg_s}"]
    if isinstance(result, dict):
        for key in ("check_result", "check_msg", "command_status", "task_id", "code", "dev_result_list"):
            if key in result:
                val = result[key]
                if key == "dev_result_list" and isinstance(val, list) and val:
                    first = val[0] if isinstance(val[0], dict) else {}
                    detail_bits.append(f"dev0_code={first.get('code')!r},dev0_task={first.get('task_id')!r}")
                else:
                    detail_bits.append(f"{key}={val!r}")
    elif result is not None:
        detail_bits.append(f"result_type={type(result).__name__}")
    return code_s, msg_s, ok, ", ".join(detail_bits)


def classify_probe_results(results: list[ProbeResult]) -> str:
    """Classify a probe run: supported_read | partial | unsupported | inconclusive."""
    if not results:
        return "inconclusive"
    any_ok = any(r.ok for r in results)
    if any_ok:
        readish = [
            r for r in results if r.ok and r.path.rstrip("/").endswith("/paramSetting") and "platform" not in r.path
        ]
        # Working surface is /openapi/paramSetting (not /platform, not necessarily only that).
        if any(r.ok and r.path == WORKING_SETTING_PATH for r in results):
            return "supported_read"
        if readish:
            return "supported_read"
        return "partial"
    if all(r.result_code is None and r.result_msg is None for r in results):
        return "inconclusive"
    return "unsupported"


async def probe_read_candidates(auth: UserAuth, device_uuid: str) -> list[ProbeResult]:
    """POST each read candidate; never raises on API-level failure."""
    out: list[ProbeResult] = []
    for label, path, builder in READ_CANDIDATES:
        body = builder(device_uuid)
        try:
            data = await auth.async_request_soft(path, body)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.info("probe %s %s raised: %s", label, path, type(err).__name__)
            out.append(
                ProbeResult(
                    label=label,
                    path=path,
                    result_code=None,
                    result_msg=None,
                    ok=False,
                    detail=f"exception={type(err).__name__}: {err}",
                )
            )
            continue
        code, msg, ok, detail = _summarize_envelope(path, data)
        _LOGGER.info("probe %s %s -> %s (logical_ok=%s)", label, path, detail, ok)
        out.append(
            ProbeResult(
                label=label,
                path=path,
                result_code=code,
                result_msg=msg,
                ok=ok,
                detail=detail,
                raw_result_data=data.get("result_data"),
            )
        )
    return out


def pick_write_path(results: list[ProbeResult]) -> tuple[str, str] | None:
    """Pick a write path/label from a successful read-shaped probe, if any."""
    for r in results:
        if r.ok and r.path == WORKING_SETTING_PATH:
            return "openapi_paramSetting_write", r.path
    for r in results:
        if not r.ok:
            continue
        if r.path.rstrip("/").endswith("/paramSetting") and "platform" not in r.path:
            return r.label.replace("_read", "_write"), r.path
    return None


async def probe_idempotent_write(
    auth: UserAuth,
    device_uuid: str,
    *,
    path: str,
    label: str,
    param_code: str = "10012",
    set_value: str = "85",
) -> ProbeResult:
    """Single write of a known-safe value (default: feed-in limitation disable=85).

    Caller must enforce :func:`write_ok_enabled`. Does not charge/discharge.
    """
    body = _body_param_setting_write(param_code, set_value)(device_uuid)
    try:
        data = await auth.async_request_soft(path, body)
    except Exception as err:  # pylint: disable=broad-except
        return ProbeResult(
            label=label,
            path=path,
            result_code=None,
            result_msg=None,
            ok=False,
            detail=f"exception={type(err).__name__}: {err}",
        )
    code, msg, ok, detail = _summarize_envelope(path, data)
    return ProbeResult(
        label=label,
        path=path,
        result_code=code,
        result_msg=msg,
        ok=ok,
        detail=detail,
        raw_result_data=data.get("result_data"),
    )


def select_dispatch_device_uuid(devices: list[dict[str, Any]]) -> str | None:
    """Pick a device uuid for probing: prefer ESS (type 14), then inverter (1), then any."""
    ess_types = {14, "14", "ENERGY_STORAGE_SYSTEM"}
    inv_types = {1, "1", "INVERTER"}

    def _uuid(device: dict[str, Any]) -> str | None:
        val = device.get("uuid")
        return str(val) if val else None

    for d in devices:
        if d.get("device_type") in ess_types:
            found = _uuid(d)
            if found:
                return found
    for d in devices:
        if d.get("device_type") in inv_types:
            found = _uuid(d)
            if found:
                return found
    for d in devices:
        found = _uuid(d)
        if found:
            return found
    return None
