"""Typed metadata for Appendix-10 dispatch parameters.

The library historically kept per-parameter encoding metadata in a nested
``dict[str, dict[str, Any]]`` (``Control.PARAMETER_SPECS``). That worked but
consumers — most notably the Home Assistant integration in
`sungrow-hass <https://github.com/KRoperUK/sungrow-hass>`_ — had to
hand-mirror the display units, min/max, enum options, and step values across
repos to build their number/select UI. Every new parameter meant editing two
codebases in lockstep and hoping the shapes agreed.

This module introduces :class:`ParameterSpec`, a frozen dataclass that models
one parameter, and :data:`PARAMETERS`, a public map keyed by canonical
parameter name. Consumers generate their UI from these records:

.. code-block:: python

    from pysolarcloud import PARAMETERS

    spec = PARAMETERS["soc_upper_limit"]
    assert spec.kind == "number"
    assert (spec.minimum, spec.maximum, spec.step) == (70.0, 100.0, 1.0)
    assert spec.battery_only is True

The old ``Control.PARAMETER_SPECS`` shape is preserved as a backwards-compatible
computed view over :data:`PARAMETERS` — existing consumers that read the
dict-of-dicts keep working unchanged (see :func:`_legacy_specs_view`).

Value encoding stays on :meth:`Control.encode_parameter <pysolarcloud.control.Control.encode_parameter>`:
consumers passing a display value get back the raw string the API expects, using
the ``wire_kind`` and ``scale`` fields on the spec.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

#: How :meth:`Control.encode_parameter <pysolarcloud.control.Control.encode_parameter>`
#: scales a display value into the raw integer the API expects.
#:
#: - ``"enum"`` — pick a raw code from :attr:`ParameterSpec.options` by name.
#: - ``"percent"`` — display in %, encoded as ``round(value * scale)``.
#: - ``"power"`` — display in W, encoded as ``round(value * scale)``.
#: - ``"duration"`` — display in s, encoded as ``round(value * scale)``.
#: - ``"ratio"`` — unitless ratio (e.g. power factor), encoded as ``round(value * scale)``.
WireKind = Literal["enum", "power", "percent", "duration", "ratio"]

#: UI widget hint consumers derive from ``wire_kind`` — ``"select"`` for enums,
#: ``"number"`` for every scalar. Exposed as :attr:`ParameterSpec.kind`.
ParameterKind = Literal["number", "select"]


@dataclass(frozen=True)
class ParameterSpec:
    """Metadata for one Appendix-10 dispatch parameter.

    Consumers generate their UI (widget type, min/max/step, enum options) from
    this record rather than hand-mirroring the metadata across repos. Encoding
    stays with :meth:`Control.encode_parameter <pysolarcloud.control.Control.encode_parameter>`,
    which reads ``wire_kind`` / ``scale`` / ``options`` here.

    The class is ``frozen`` so consumers can safely cache references without
    fearing mutation from another caller.
    """

    #: On-wire parameter code the iSolarCloud API uses (e.g. ``"10001"`` for
    #: ``soc_upper_limit``). Matches the ``code`` field in ``Appendix 10``.
    code: str

    #: How :meth:`Control.encode_parameter <pysolarcloud.control.Control.encode_parameter>`
    #: scales a display value to the raw integer the API expects. See :data:`WireKind`.
    wire_kind: WireKind

    #: Display unit (``"%"``, ``"W"``, ``"s"``, ...). ``None`` for unitless
    #: parameters like power factor.
    unit: str | None = None

    #: Multiplier from display value to the raw integer. Ignored for enums.
    #: SOC / ratio percents are tenths (700-1000 = 70-100%, ``scale=10``); watts
    #: are direct (``scale=1``); power factor is thousandths (``scale=1000``).
    scale: float = 1

    #: Inclusive lower bound on the display value. ``None`` = open. Enforced by
    #: ``encode_parameter`` before the value reaches hardware.
    minimum: float | None = None

    #: Inclusive upper bound on the display value. ``None`` = open.
    maximum: float | None = None

    #: Suggested UI step for number widgets, in display units. ``None`` means
    #: consumers should pick a sensible default (typically 1 for integers,
    #: 0.01 for floats).
    step: float | None = None

    #: For :data:`wire_kind` ``"enum"``, an ordered ``{option_name: raw_code}``
    #: map. Option names are lowercase canonical (e.g. ``"charge"``,
    #: ``"enable"``) matching what ``encode_parameter`` accepts. Consumers
    #: wanting user-friendly labels should map names to their own translations.
    #: ``None`` for non-enum parameters.
    options: dict[str, str] | None = None

    #: Advisory hint: the parameter only takes effect on plants with a battery
    #: attached. Consumers building a UI may hide the entity on battery-less
    #: plants; the API itself accepts the write either way, so this is not a
    #: hard constraint. See sungrow-hass #148 for the motivating case.
    battery_only: bool = False

    @property
    def kind(self) -> ParameterKind:
        """UI widget hint: ``"select"`` for enum params, ``"number"`` for everything else.

        Derived from :attr:`wire_kind` so the two never disagree — enum-encoded
        parameters get a select widget; scalar-encoded ones get a number widget.
        """
        return "select" if self.wire_kind == "enum" else "number"


# ---------------------------------------------------------------------------
# The public map — one row per settable Appendix-10 parameter.
# ---------------------------------------------------------------------------

#: Authoritative typed metadata for every settable Appendix-10 dispatch parameter.
#:
#: Keys are canonical parameter names (e.g. ``"soc_upper_limit"``); values are
#: :class:`ParameterSpec` records. Iterate to build a UI; look up by name to
#: query one parameter. See :data:`Control.PARAMETERS <pysolarcloud.control.Control.PARAMETERS>`
#: for the class-attribute alias.
PARAMETERS: dict[str, ParameterSpec] = {
    # --- Battery state-of-charge limits (Appendix 10 §10001-10002) ---
    "soc_upper_limit": ParameterSpec(
        code="10001",
        wire_kind="percent",
        unit="%",
        scale=10,
        minimum=70,
        maximum=100,
        step=1,
        battery_only=True,
    ),
    "soc_lower_limit": ParameterSpec(
        code="10002",
        wire_kind="percent",
        unit="%",
        scale=10,
        minimum=0,
        maximum=50,
        step=1,
        battery_only=True,
    ),
    # --- Energy management + battery dispatch (Appendix 10 §10003-10005) ---
    # Writing 10004/10005 alone leaves the plant in Self-consumption and the
    # inverter silently ignores the command; 10003 must switch out of it. See
    # sungrow-hass #231.
    "energy_management_mode": ParameterSpec(
        code="10003",
        wire_kind="enum",
        options={"self_consumption": "0", "compulsory": "2", "external_dispatch": "3", "vpp": "4"},
        battery_only=True,
    ),
    "charge_discharge_command": ParameterSpec(
        code="10004",
        wire_kind="enum",
        options={"charge": "170", "discharge": "187", "stop": "204"},
        battery_only=True,
    ),
    "charge_discharge_power": ParameterSpec(
        code="10005",
        wire_kind="power",
        unit="W",
        scale=1,
        minimum=0,
        maximum=5000,
        step=100,
        battery_only=True,
    ),
    # --- Grid-side dispatch (Appendix 10 §10007-10008, 10012-10014) ---
    "limited_power_switch": ParameterSpec(
        code="10007",
        wire_kind="enum",
        options={"enable": "170", "disable": "85"},
    ),
    "active_power_limit_ratio": ParameterSpec(
        code="10008",
        wire_kind="percent",
        unit="%",
        scale=10,
        minimum=0,
        maximum=100,
        step=1,
    ),
    "feed_in_limitation": ParameterSpec(
        code="10012",
        wire_kind="enum",
        options={"enable": "170", "disable": "85"},
    ),
    "feed_in_limitation_value": ParameterSpec(
        code="10013",
        wire_kind="power",
        unit="W",
        scale=1,
        minimum=0,
        # No upper bound — consumers size the slider from the device's rated
        # power and clamp there. The library refuses to invent a ceiling.
        maximum=None,
        step=100,
    ),
    "feed_in_limitation_ratio": ParameterSpec(
        code="10014",
        wire_kind="percent",
        unit="%",
        scale=10,
        minimum=0,
        maximum=100,
        step=1,
    ),
    # --- EMS heartbeat (Appendix 10 §10017) ---
    "external_ems_heartbeat": ParameterSpec(
        code="10017",
        wire_kind="duration",
        unit="s",
        scale=1,
        minimum=1,
        maximum=1000,
        step=1,
    ),
    # --- Battery-first mode (Appendix 10 §10024) ---
    "battery_first": ParameterSpec(
        code="10024",
        wire_kind="enum",
        options={"enable": "170", "disable": "85"},
        battery_only=True,
    ),
    # --- Reactive power / power factor (Appendix 10 §10009-10036) ---
    # Mode gate: q_t needs mode=q_t; pf needs mode=pf; reactive_response and
    # reactive_power_regulation_time need any non-OFF mode.
    "reactive_power_regulation_mode": ParameterSpec(
        code="10009",
        wire_kind="enum",
        options={"off": "85", "pf": "161", "q_t": "162", "q_p": "163", "q_u": "164"},
    ),
    "q_t": ParameterSpec(
        code="10010",
        wire_kind="percent",
        unit="%",
        scale=10,
        minimum=-60,
        maximum=60,
        step=1,
    ),
    "reactive_response": ParameterSpec(
        code="10034",
        wire_kind="enum",
        options={"enable": "170", "disable": "85"},
    ),
    "reactive_power_regulation_time": ParameterSpec(
        code="10035",
        wire_kind="duration",
        unit="s",
        scale=10,
        minimum=0.1,
        maximum=600,
        step=0.1,
    ),
    "pf": ParameterSpec(
        code="10036",
        wire_kind="ratio",
        unit=None,
        scale=1000,
        minimum=-1,
        maximum=1,
        step=0.01,
    ),
    # --- Forced charging window (Appendix 10 §10065, 10071, 10076) ---
    "forced_charging": ParameterSpec(
        code="10065",
        wire_kind="enum",
        options={"enable": "170", "disable": "85"},
        battery_only=True,
    ),
    "forced_charging_target_soc_1": ParameterSpec(
        code="10071",
        wire_kind="percent",
        unit="%",
        # These SOC-target codes take a direct percent (not tenths), unlike
        # 10001/10002 which use scale=10.
        scale=1,
        minimum=0,
        maximum=100,
        step=1,
        battery_only=True,
    ),
    "forced_charging_target_soc_2": ParameterSpec(
        code="10076",
        wire_kind="percent",
        unit="%",
        scale=1,
        minimum=0,
        maximum=100,
        step=1,
        battery_only=True,
    ),
}


def _legacy_specs_view() -> dict[str, dict[str, Any]]:
    """Return the pre-typed ``PARAMETER_SPECS`` dict-of-dicts shape.

    Preserved so consumers that were reading the old ``Control.PARAMETER_SPECS``
    keep working without changes. New code should use :data:`PARAMETERS`
    directly (and :class:`ParameterSpec` fields) rather than this dict view.

    Field mapping (old key → source):

    - ``"code"``       ← :attr:`ParameterSpec.code`
    - ``"kind"``       ← :attr:`ParameterSpec.wire_kind` (name preserved from the
                        legacy shape; ``ParameterSpec.kind`` is the *new* UI hint
                        and is deliberately not exposed here to avoid confusion).
    - ``"unit"``       ← :attr:`ParameterSpec.unit`
    - ``"scale"``      ← :attr:`ParameterSpec.scale`
    - ``"min"``        ← :attr:`ParameterSpec.minimum`
    - ``"max"``        ← :attr:`ParameterSpec.maximum`
    - ``"values"``     ← :attr:`ParameterSpec.options` (enum only)
    """
    view: dict[str, dict[str, Any]] = {}
    for name, spec in PARAMETERS.items():
        row: dict[str, Any] = {"code": spec.code, "kind": spec.wire_kind}
        if spec.wire_kind == "enum":
            # Legacy field name is "values" not "options".
            row["values"] = dict(spec.options or {})
        else:
            if spec.unit is not None:
                row["unit"] = spec.unit
            row["scale"] = spec.scale
            row["min"] = spec.minimum
            row["max"] = spec.maximum
        view[name] = row
    return view


# Module-level frozen view so repeat callers don't rebuild the dict per lookup.
# ``PARAMETERS`` itself owns the mutable-look defaults; this cached copy is
# effectively read-only for consumers that keep to normal dict operations.
_LEGACY_SPECS: dict[str, dict[str, Any]] = _legacy_specs_view()


__all__ = ["PARAMETERS", "ParameterKind", "ParameterSpec", "WireKind"]
