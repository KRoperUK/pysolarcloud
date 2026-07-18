"""Experimental probes for user-account EMS control endpoints (sungrow-hass #271).

**Not a stable public API.** Used by live/unit tests to answer whether a plain
iSolarCloud user session can read/write control parameters (Phase 5 spike).

Clean-room only: path *names* and field names are protocol facts; no GPL source
was used. Payloads for known OpenAPI-shaped tasks mirror Appendix 10 parameter
codes (e.g. ``10003`` energy management mode) already used by :mod:`pysolarcloud.control`.
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

# Energy Management Mode (Appendix 10) — the dispatch gate used by the OAuth Control path.
_EMS_MODE_CODE = "10003"
_EXPIRE_SECONDS = 120

# Env gate for the optional single write step (default off).
WRITE_OK_ENV = "SUNGROW_USER_WRITE_OK"


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


def _body_param_setting_read() -> BodyBuilder:
    def build(device_uuid: str) -> dict[str, Any]:
        return {
            "set_type": 2,
            "uuid": str(device_uuid),
            "task_name": _task_name("Readback"),
            "expire_second": _EXPIRE_SECONDS,
            "param_list": [{"param_code": _EMS_MODE_CODE, "set_value": ""}],
        }

    return build


def _body_param_setting_write(set_value: str) -> BodyBuilder:
    def build(device_uuid: str) -> dict[str, Any]:
        return {
            "set_type": 0,
            "uuid": str(device_uuid),
            "task_name": _task_name("Update"),
            "expire_second": _EXPIRE_SECONDS,
            "param_list": [{"param_code": _EMS_MODE_CODE, "set_value": str(set_value)}],
        }

    return build


def _body_get_device_param() -> BodyBuilder:
    def build(device_uuid: str) -> dict[str, Any]:
        return {
            "uuid": str(device_uuid),
            "param_list": [{"param_code": _EMS_MODE_CODE}],
        }

    return build


# Read/check candidates: OpenAPI paths (negative control) + /v1 app-style guesses.
READ_CANDIDATES: list[tuple[str, str, BodyBuilder]] = [
    ("openapi_paramSettingCheck_read", "/openapi/platform/paramSettingCheck", _body_param_check(2)),
    ("openapi_paramSettingCheck_update", "/openapi/platform/paramSettingCheck", _body_param_check(0)),
    ("openapi_paramSetting_read", "/openapi/platform/paramSetting", _body_param_setting_read()),
    ("v1_dev_paramSettingCheck_read", "/v1/devService/paramSettingCheck", _body_param_check(2)),
    ("v1_dev_paramSettingCheck_update", "/v1/devService/paramSettingCheck", _body_param_check(0)),
    ("v1_dev_paramSetting_read", "/v1/devService/paramSetting", _body_param_setting_read()),
    ("v1_dev_getDeviceParam", "/v1/devService/getDeviceParam", _body_get_device_param()),
    ("v1_dev_setDeviceParam_check_shape", "/v1/devService/setDeviceParam", _body_param_setting_read()),
    ("v1_device_paramSettingCheck_read", "/v1/deviceService/paramSettingCheck", _body_param_check(2)),
    ("v1_device_paramSetting_read", "/v1/deviceService/paramSetting", _body_param_setting_read()),
]


def write_ok_enabled() -> bool:
    """True when the optional live write gate is set."""
    return os.getenv(WRITE_OK_ENV, "").strip() in {"1", "true", "TRUE", "yes", "YES"}


def _envelope_ok(data: dict[str, Any]) -> bool:
    """True when the outer iSolarCloud envelope claims success."""
    return data.get("result_msg") == "success" or str(data.get("result_code")) == "1"


def _control_logical_ok(path: str, data: dict[str, Any]) -> bool:
    """True when a control probe did useful work — not just an outer success shell.

    Live findings (#271): ``/v1/devService/paramSetting`` often returns envelope
    success with ``result_data: {code: "4"}`` and no ``task_id``. That is a logical
    failure (task not accepted). Capability checks need ``check_result == "1"``.
    """
    if not _envelope_ok(data):
        return False
    result = data.get("result_data")
    path = path.rstrip("/")
    if path.endswith("paramSettingCheck"):
        return isinstance(result, dict) and str(result.get("check_result")) == "1"
    if path.endswith("paramSetting") or path.endswith("setDeviceParam"):
        if not isinstance(result, dict):
            return False
        # Explicit device/task codes (OpenAPI uses "1" for ok on nested objects).
        nested = result.get("code")
        if nested is not None and str(nested) not in {"1", "success"}:
            return False
        if result.get("task_id"):
            return True
        dev_list = result.get("dev_result_list")
        if isinstance(dev_list, list) and dev_list:
            return True
        if result.get("param_list"):
            return True
        # Bare {code: "1"} without task payload is weak but treat as ok.
        return nested is not None and str(nested) in {"1", "success"}
    if path.endswith("getDeviceParam"):
        return bool(result)
    return True


def _summarize_envelope(path: str, data: dict[str, Any]) -> tuple[str | None, str | None, bool, str]:
    """Return (code, msg, ok, short_detail) without secrets."""
    code = data.get("result_code")
    msg = data.get("result_msg")
    code_s = None if code is None else str(code)
    msg_s = None if msg is None else str(msg)
    ok = _control_logical_ok(path, data)
    result = data.get("result_data")
    detail_bits = [f"code={code_s}", f"msg={msg_s}"]
    if isinstance(result, dict):
        # Surface common control-task fields without dumping everything.
        for key in ("check_result", "check_msg", "command_status", "task_id", "code", "dev_result_list"):
            if key in result:
                val = result[key]
                if key == "dev_result_list" and isinstance(val, list):
                    detail_bits.append(f"dev_results={len(val)}")
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
        # Prefer a path that looks like an actual param read/task, not only *Check.
        readish = [
            r
            for r in results
            if r.ok
            and (
                r.path.rstrip("/").endswith("/paramSetting")
                or r.path.rstrip("/").endswith("/getDeviceParam")
                or r.path.rstrip("/").endswith("/setDeviceParam")
            )
        ]
        return "supported_read" if readish else "partial"
    # All failed: if every response is empty/malformed treat as inconclusive.
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
        if not r.ok:
            continue
        if r.path.endswith("/paramSetting"):
            return r.label.replace("_read", "_write"), r.path
        if "setDeviceParam" in r.path:
            return r.label + "_write", r.path
    return None


async def probe_idempotent_write(
    auth: UserAuth,
    device_uuid: str,
    *,
    path: str,
    label: str,
    set_value: str = "0",
) -> ProbeResult:
    """Attempt a single EMS-mode write (default self-consumption raw ``0``).

    Caller must enforce :func:`write_ok_enabled`. Does not charge/discharge.
    """
    body = _body_param_setting_write(set_value)(device_uuid)
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
