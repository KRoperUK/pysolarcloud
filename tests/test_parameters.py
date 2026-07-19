"""Tests for the typed :class:`ParameterSpec` model and :data:`PARAMETERS` map (#71).

The library re-exports :class:`ParameterSpec` and :data:`PARAMETERS` at package top
level; the ``Control`` class also exposes them as class attributes for discoverability
alongside the existing ``config_parameters`` / ``PARAMETER_SPECS`` legacy views. These
tests cover the typed record's shape, the legacy ``dict[str, dict[str, Any]]`` view's
backwards-compat contract, and a few invariants over the full :data:`PARAMETERS` map
so a future edit can't silently break consumers.
"""

from __future__ import annotations

import dataclasses

import pytest

import pysolarcloud
from pysolarcloud import PARAMETERS, ParameterSpec
from pysolarcloud.control import Control

# ---------------------------------------------------------------------------
# ParameterSpec — dataclass surface
# ---------------------------------------------------------------------------


def test_parameter_spec_is_frozen():
    """Consumers can cache references without fearing mutation from another caller."""
    spec = PARAMETERS["soc_upper_limit"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.minimum = 0  # type: ignore[misc]


def test_parameter_spec_kind_is_derived_from_wire_kind():
    """``kind`` is the UI hint; it can't disagree with ``wire_kind``."""
    for name, spec in PARAMETERS.items():
        expected = "select" if spec.wire_kind == "enum" else "number"
        assert spec.kind == expected, f"{name}.kind should be {expected!r}"


def test_parameter_spec_number_shape():
    """A representative numeric spec exposes the full number-widget surface (#71)."""
    spec = PARAMETERS["charge_discharge_power"]
    assert spec.kind == "number"
    assert spec.wire_kind == "power"
    assert spec.code == "10005"
    assert spec.unit == "W"
    assert spec.scale == 1
    assert (spec.minimum, spec.maximum, spec.step) == (0, 5000, 100)
    assert spec.options is None
    assert spec.battery_only is True


def test_parameter_spec_select_shape():
    """A representative enum spec exposes an ``options`` map and no numeric fields (#71)."""
    spec = PARAMETERS["charge_discharge_command"]
    assert spec.kind == "select"
    assert spec.wire_kind == "enum"
    assert spec.code == "10004"
    assert spec.options == {"charge": "170", "discharge": "187", "stop": "204"}
    assert spec.battery_only is True


def test_parameter_spec_unitless_ratio():
    """``pf`` is unitless and has a sub-integer step for fine-grained factor tuning."""
    spec = PARAMETERS["pf"]
    assert spec.kind == "number"
    assert spec.wire_kind == "ratio"
    assert spec.unit is None
    assert (spec.minimum, spec.maximum, spec.step) == (-1, 1, 0.01)
    assert spec.scale == 1000


# ---------------------------------------------------------------------------
# Legacy PARAMETER_SPECS view — backwards compat contract
# ---------------------------------------------------------------------------


def test_legacy_specs_view_preserves_pre_71_shape():
    """``Control.PARAMETER_SPECS`` keeps the pre-typed ``dict[str, dict[str, Any]]``
    shape so consumers that hadn't migrated to :class:`ParameterSpec` still work."""
    spec = Control.PARAMETER_SPECS["soc_upper_limit"]
    assert spec == {"code": "10001", "kind": "percent", "unit": "%", "scale": 10, "min": 70, "max": 100}


def test_legacy_specs_view_enum_uses_values_key():
    """Legacy dict view keeps the ``values`` key name (not ``options``) for enums."""
    spec = Control.PARAMETER_SPECS["charge_discharge_command"]
    assert spec["kind"] == "enum"
    assert spec["values"] == {"charge": "170", "discharge": "187", "stop": "204"}


def test_legacy_specs_view_covers_every_parameter():
    """Every entry in the typed :data:`PARAMETERS` map has a matching legacy dict row."""
    assert set(Control.PARAMETER_SPECS.keys()) == set(PARAMETERS.keys())


# ---------------------------------------------------------------------------
# Top-level re-exports and Control class attribute
# ---------------------------------------------------------------------------


def test_public_api_re_exports():
    """``ParameterSpec`` and ``PARAMETERS`` are importable from the package root."""
    assert pysolarcloud.PARAMETERS is PARAMETERS
    assert pysolarcloud.ParameterSpec is ParameterSpec


def test_control_class_exposes_parameters():
    """``Control.PARAMETERS`` is the same map as the module-level export."""
    assert Control.PARAMETERS is PARAMETERS


def test_encode_parameter_reads_typed_spec_bounds():
    """``encode_parameter`` uses :attr:`ParameterSpec.minimum` / ``.maximum`` /
    ``.scale`` fields from the typed map (not the legacy dict)."""
    # Boundary check via the typed spec's minimum.
    assert Control.encode_parameter("soc_upper_limit", 70) == "700"
    # Out-of-range via the typed spec's maximum.
    with pytest.raises(ValueError, match="soc_upper_limit"):
        Control.encode_parameter("soc_upper_limit", 101)


# ---------------------------------------------------------------------------
# Invariants over the whole map
# ---------------------------------------------------------------------------


def test_every_spec_has_a_code():
    """No parameter can be nameless-on-the-wire — enforcing on the map."""
    for name, spec in PARAMETERS.items():
        assert spec.code, f"{name}: missing wire code"


def test_every_spec_code_is_unique():
    """Two parameter names must not map to the same on-wire code."""
    codes = [spec.code for spec in PARAMETERS.values()]
    assert len(codes) == len(set(codes)), "duplicate code detected"


def test_every_enum_spec_has_options():
    """A ``wire_kind='enum'`` parameter without options can't encode."""
    for name, spec in PARAMETERS.items():
        if spec.wire_kind == "enum":
            assert spec.options, f"{name}: enum spec missing options"
        else:
            assert spec.options is None, f"{name}: non-enum spec must not carry options"


def test_every_numeric_spec_has_scale():
    """Non-enum specs must specify a scale (encode_parameter multiplies by it)."""
    for name, spec in PARAMETERS.items():
        if spec.wire_kind != "enum":
            assert spec.scale is not None, f"{name}: numeric spec missing scale"


def test_every_bounded_spec_has_sensible_range():
    """Where both min and max are given, minimum ≤ maximum."""
    for name, spec in PARAMETERS.items():
        if spec.minimum is not None and spec.maximum is not None:
            assert spec.minimum <= spec.maximum, f"{name}: minimum > maximum"


def test_encode_parameter_works_for_every_enum_option():
    """Every option in every enum spec round-trips through ``encode_parameter``."""
    for name, spec in PARAMETERS.items():
        if spec.wire_kind != "enum":
            continue
        for option_name, raw_code in (spec.options or {}).items():
            # By canonical option name.
            assert Control.encode_parameter(name, option_name) == raw_code
            # By raw code passthrough.
            assert Control.encode_parameter(name, raw_code) == raw_code
