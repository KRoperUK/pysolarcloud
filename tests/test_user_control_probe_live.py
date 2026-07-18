"""Live probe + UserControl read (and optional write) for #271.

Read-only by default. Optional single idempotent write when SUNGROW_USER_WRITE_OK=1
(re-applies feed-in limitation disable=85 observed on the probe plant).

Run with:  pytest -m live tests/test_user_control_probe_live.py -v -s
Requires SUNGROW_USER_ACCOUNT / SUNGROW_USER_PASSWORD (and optionally SUNGROW_HOST).
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from pysolarcloud import PySolarCloudException, UserAuth, UserControl
from pysolarcloud import user_control_probe as probe

_LOGGER = logging.getLogger(__name__)


@pytest.mark.live
async def test_user_control_endpoint_probe(live_user_credentials):
    """Login → probe candidates → UserControl read; optional gated write."""
    async with UserAuth(
        live_user_credentials["host"],
        live_user_credentials["email"],
        live_user_credentials["password"],
    ) as auth:
        plants = await auth.async_get_plants()
        assert plants, "account has no plants; cannot probe control"
        plant = plants[0]
        ps_id = plant.get("ps_id")
        assert ps_id is not None

        devices = await auth.async_get_devices(ps_id)
        assert devices, f"plant {ps_id} has no devices"
        device_uuid = probe.select_dispatch_device_uuid(devices)
        assert device_uuid, "no device uuid in plant device list"

        _LOGGER.warning(
            "CONTROL PROBE plant_id=%s device_uuid=%s device_count=%s",
            ps_id,
            device_uuid,
            len(devices),
        )

        results = await probe.probe_read_candidates(auth, device_uuid)
        classification = probe.classify_probe_results(results)

        lines = [f"classification={classification}"]
        for r in results:
            lines.append(f"  [{r.label}] {r.path} ok={r.ok} {r.detail}")
        report = "\n".join(lines)
        print("\n=== user control probe report (#271) ===\n" + report + "\n===\n")

        # Full client path: read common inverter params via UserControl.
        control = UserControl(auth)
        assert await control.async_check_read_support(device_uuid)
        rows = await control.async_read_parameters(device_uuid, ["10011", "10008", "10012"])
        print("UserControl read:")
        for row in rows:
            print(f"  {row.get('id')} {row.get('name')} = {row.get('value')!r} {row.get('unit') or ''}")
        assert rows, "UserControl read returned no parameters"
        codes = {str(r.get("id")) for r in rows}
        assert "10011" in codes or "10008" in codes or "10012" in codes

        write_result = None
        if probe.write_ok_enabled():
            picked = probe.pick_write_path(results)
            if picked is None:
                print("WRITE SKIPPED: no successful read-shaped path to reuse\n")
            else:
                label, path = picked
                # Idempotent: feed-in limitation disable (85) as observed on PV plant.
                write_result = await probe.probe_idempotent_write(
                    auth,
                    device_uuid,
                    path=path,
                    label=label,
                    param_code="10012",
                    set_value="85",
                )
                print(
                    f"WRITE probe [{write_result.label}] {write_result.path} "
                    f"ok={write_result.ok} {write_result.detail}\n"
                )
                if write_result.ok:
                    # Server rejects rapid re-submit with check_result 9 ("do not repeat").
                    await asyncio.sleep(8)
                    try:
                        confirm = await control.async_read_parameters(device_uuid, ["10012"])
                        print(f"WRITE confirm read-back: {confirm}")
                    except PySolarCloudException as err:
                        print(f"WRITE confirm read-back skipped (likely rate-limit): {err}")
        else:
            print(f"WRITE not attempted (set {probe.WRITE_OK_ENV}=1 to enable)\n")

        assert results, "probe produced no results"
        assert classification == "supported_read", (
            f"expected supported_read after /openapi/paramSetting discovery, got {classification}"
        )
        if write_result is not None:
            assert write_result.label
