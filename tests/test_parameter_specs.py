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


def test_out_of_range_value_raises():
    # soc_upper_limit min is 70% -> 50% must be rejected before it reaches hardware (#13).
    with pytest.raises(ValueError, match="soc_upper_limit"):
        Control.encode_parameter("soc_upper_limit", 50)
    # soc_lower_limit max is 50%.
    with pytest.raises(ValueError, match="soc_lower_limit"):
        Control.encode_parameter("soc_lower_limit", 60)
    # charge_discharge_power range is 0..5000 W.
    with pytest.raises(ValueError, match="charge_discharge_power"):
        Control.encode_parameter("charge_discharge_power", -100)
    with pytest.raises(ValueError, match="charge_discharge_power"):
        Control.encode_parameter("charge_discharge_power", 6000)


def test_in_range_boundary_values_encode():
    # Boundaries are inclusive and still encode with the usual scaling.
    assert Control.encode_parameter("soc_upper_limit", 70) == "700"
    assert Control.encode_parameter("soc_upper_limit", 100) == "1000"
    assert Control.encode_parameter("charge_discharge_power", 0) == "0"
    assert Control.encode_parameter("charge_discharge_power", 5000) == "5000"


def test_open_upper_bound_allows_large_value():
    # feed_in_limitation_value has min 0 but no upper bound (max None) -> large value OK.
    assert Control.encode_parameter("feed_in_limitation_value", 100000) == "100000"
    with pytest.raises(ValueError, match="feed_in_limitation_value"):
        Control.encode_parameter("feed_in_limitation_value", -1)


def test_max_charge_discharge_power_not_silently_unscaled():
    # 10091/10092 had no PARAMETER_SPECS, so encode_parameter emitted the display value
    # unscaled. They are removed from config_parameters until real device limits are known (#13/#16).
    assert "max_charging_power" not in Control.config_parameters.values()
    assert "max_discharging_power" not in Control.config_parameters.values()
    assert "10091" not in Control.config_parameters
    assert "10092" not in Control.config_parameters


async def test_async_set_parameter_encodes_then_writes():
    control = Control(MagicMock())
    control.async_update_parameters = AsyncMock(return_value=[])
    await control.async_set_parameter("dev-1", "soc_upper_limit", 90)
    control.async_update_parameters.assert_awaited_once_with("dev-1", {"soc_upper_limit": "900"})


# --- Reactive-power / power-factor control (Appendix 10) ---


def test_reactive_power_regulation_mode_enum():
    # 85 OFF, 161 PF, 162 Q(t), 163 Q(P), 164 Q(U).
    assert Control.encode_parameter("reactive_power_regulation_mode", "off") == "85"
    assert Control.encode_parameter("reactive_power_regulation_mode", "PF") == "161"
    assert Control.encode_parameter("reactive_power_regulation_mode", "q_t") == "162"
    assert Control.encode_parameter("reactive_power_regulation_mode", "q_p") == "163"
    assert Control.encode_parameter("reactive_power_regulation_mode", "q_u") == "164"
    # Raw-code passthrough works too.
    assert Control.encode_parameter("reactive_power_regulation_mode", "164") == "164"


def test_q_t_is_tenths_of_a_percent_signed():
    # API range -600..600 = -60%..60% -> scale 10, signed.
    assert Control.encode_parameter("q_t", 30) == "300"
    assert Control.encode_parameter("q_t", -60) == "-600"
    assert Control.encode_parameter("q_t", 0) == "0"
    with pytest.raises(ValueError, match="q_t"):
        Control.encode_parameter("q_t", 61)
    with pytest.raises(ValueError, match="q_t"):
        Control.encode_parameter("q_t", -61)


def test_pf_is_thousandths_signed():
    # API range -1000..1000 = -1..1 -> scale 1000, signed.
    assert Control.encode_parameter("pf", 0.9) == "900"
    assert Control.encode_parameter("pf", -1) == "-1000"
    assert Control.encode_parameter("pf", 1) == "1000"
    with pytest.raises(ValueError, match="pf"):
        Control.encode_parameter("pf", 1.5)


def test_reactive_response_and_time():
    assert Control.encode_parameter("reactive_response", "enable") == "170"
    assert Control.encode_parameter("reactive_response", "disable") == "85"
    # 1..6000 = 0.1s..600s -> scale 10.
    assert Control.encode_parameter("reactive_power_regulation_time", 60) == "600"
    assert Control.encode_parameter("reactive_power_regulation_time", 0.1) == "1"
    with pytest.raises(ValueError, match="reactive_power_regulation_time"):
        Control.encode_parameter("reactive_power_regulation_time", 601)
