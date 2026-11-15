"""Tests for the shared realtime point representation (issue #144).

``Plants._format_measure_point`` (OpenAPI path) and ``UserAuth._format_point``
(user-account path) must agree on one representation — ``float | str | None``
values, ``unit``/``name`` ``None`` when the response ``point_dict`` lacks the
entry — before any consumer starts depending on the types. The only intentional
split is the ``code`` key: the OpenAPI path resolves it from the measure-point
catalog, while the user path has no static code map (the
``_async_get_device_realtime_user_fallback`` re-key injects it when mapping onto
the OpenAPI shape).
"""

from unittest.mock import MagicMock

import pytest

from pysolarcloud.plants import Plants
from pysolarcloud.user_auth import UserAuth

POINT_DICT = {"10001": {"point_unit": "kW", "point_name": "PV Power"}}
CODES = {"10001": "pv_power"}


@pytest.fixture
def plants():
    return Plants(MagicMock())


def _openapi(plants, point_id, value, point_dict=POINT_DICT):
    return plants._format_measure_point(point_id, value, point_dict, CODES)


def _user(_plants, point_id, value, point_dict=POINT_DICT):
    return UserAuth._format_point(point_id, value, point_dict)


FORMATS = [_openapi, _user]


# --------------------------------------------------------------------------- #
# Shared representation: both paths agree
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("format_point", FORMATS)
def test_numeric_string_coerces_to_float(plants, format_point):
    """Numeric strings become floats on both paths."""
    assert format_point(plants, "10001", "12.5")["value"] == 12.5


@pytest.mark.parametrize("format_point", FORMATS)
def test_display_string_passes_through(plants, format_point):
    """Non-numeric display strings (e.g. status text) survive verbatim on both paths."""
    assert format_point(plants, "10001", "Standby")["value"] == "Standby"


@pytest.mark.parametrize("format_point", FORMATS)
def test_none_stays_none(plants, format_point):
    """A missing reading is None, not 0.0, on both paths."""
    assert format_point(plants, "10001", None)["value"] is None


@pytest.mark.parametrize("format_point", FORMATS)
def test_unexpected_type_passes_through(plants, format_point):
    """A non-string payload (bad server data) is passed through, never raised, on both paths."""
    payload = {"unexpected": "shape"}
    assert format_point(plants, "10001", payload)["value"] == payload


@pytest.mark.parametrize("format_point", FORMATS)
def test_missing_point_dict_entry_gives_none_unit_and_name(plants, format_point):
    """Without a point_dict entry, unit/name are None (not '') on both paths."""
    point = format_point(plants, "99999", "1", {})
    assert point["unit"] is None
    assert point["name"] is None


# --------------------------------------------------------------------------- #
# Intentional split: the ``code`` key
# --------------------------------------------------------------------------- #


def test_openapi_path_resolves_code(plants):
    """The OpenAPI path resolves the human code from the measure-point catalog."""
    point = _openapi(plants, "10001", "12.5")
    assert point["code"] == "pv_power"
    assert point["unit"] == "kW"
    assert point["name"] == "PV Power"


def test_user_path_has_no_code_key(plants):
    """The user path emits no ``code`` key — it has no static code map."""
    point = _user(plants, "10001", "12.5")
    assert "code" not in point
    assert point["unit"] == "kW"
    assert point["name"] == "PV Power"
