"""Tests for control-parameter value encoding (KRoperUK fork)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from pysolarcloud.control import Control


def test_power_is_sent_verbatim_in_watts():
    # charge_discharge_power is watts (scale 1) per Appendix 10 (0W to 5000W).
    assert Control.encode_parameter("charge_discharge_power", 2500) == "2500"
    assert Control.encode_parameter("feed_in_limitation_value", 3700) == "3700"


def test_soc_and_ratios_are_tenths_of_a_percent():
    # API range 700-1000 = 70-100%, etc. -> scale 10.
    assert Control.encode_parameter("soc_upper_limit", 90) == "900"
    assert Control.encode_parameter("soc_lower_limit", 20) == "200"
    assert Control.encode_parameter("active_power_limit_ratio", 100) == "1000"
    assert Control.encode_parameter("feed_in_limitation_ratio", 80) == "800"


def test_forced_charge_target_is_direct_percent():
    assert Control.encode_parameter("forced_charging_target_soc_1", 75) == "75"
    assert Control.encode_parameter("forced_charging_target_soc_2", 50) == "50"


def test_enum_by_option_name_case_insensitive():
    assert Control.encode_parameter("charge_discharge_command", "charge") == "170"
    assert Control.encode_parameter("charge_discharge_command", "Discharge") == "187"
    assert Control.encode_parameter("charge_discharge_command", "STOP") == "204"
    assert Control.encode_parameter("feed_in_limitation", "enable") == "170"
    assert Control.encode_parameter("battery_first", "disable") == "85"


def test_enum_raw_code_passthrough():
    assert Control.encode_parameter("charge_discharge_command", "170") == "170"


def test_unknown_enum_option_raises():
    with pytest.raises(ValueError, match="Unknown option"):
        Control.encode_parameter("charge_discharge_command", "explode")


def test_non_numeric_numeric_raises():
    with pytest.raises(ValueError, match="numeric value"):
        Control.encode_parameter("soc_upper_limit", "ninety")


def test_unknown_parameter_passthrough():
    assert Control.encode_parameter("not_a_real_param", 42) == "42"


async def test_async_set_parameter_encodes_then_writes():
    control = Control(MagicMock())
    control.async_update_parameters = AsyncMock(return_value=[])
    await control.async_set_parameter("dev-1", "soc_upper_limit", 90)
    control.async_update_parameters.assert_awaited_once_with("dev-1", {"soc_upper_limit": "900"})
