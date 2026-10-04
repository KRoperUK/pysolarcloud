"""Tests for the packaged measure-point catalog (:mod:`pysolarcloud.measure_points`).

The catalog was converted from the sungrow-hass integration's ``measure_points_data.py``
(KRoperUK/sungrow-hass#484) by ``scripts/gen_measure_points.py``. The integration is not
importable here, so conversion fidelity is pinned through counts and spot checks taken
from that source; a change to any count is a catalog edit and should be deliberate.
"""

from __future__ import annotations

import ast
import dataclasses
import json
from collections import Counter
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

import pysolarcloud
from pysolarcloud import MeasurePoint, MeasurePointCatalog, load_measure_points
from pysolarcloud.measure_points import SCHEMA_VERSION, _parse
from pysolarcloud.plants import Plants

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def catalog() -> MeasurePointCatalog:
    return load_measure_points()


def _document() -> dict[str, Any]:
    text = files("pysolarcloud").joinpath("data", "measure_points.json").read_text(encoding="utf-8")
    document: dict[str, Any] = json.loads(text)
    return document


# --- Packaging ----------------------------------------------------------------


def test_catalog_is_an_importlib_resource():
    resource = files("pysolarcloud").joinpath("data", "measure_points.json")
    assert resource.is_file()
    assert _document()["schema_version"] == SCHEMA_VERSION == 1


def test_catalog_is_declared_as_package_data_for_wheel_and_sdist():
    """The JSON must reach the wheel (package_data) and the sdist (MANIFEST.in)."""
    tree = ast.parse((ROOT / "setup.py").read_text(encoding="utf-8"))
    package_data = next(
        ast.literal_eval(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.keyword) and node.arg == "package_data"
    )
    package_dir = ROOT / "src" / "pysolarcloud"
    matched = [p for pattern in package_data["pysolarcloud"] for p in package_dir.glob(pattern)]
    assert package_dir / "data" / "measure_points.json" in matched

    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
    assert "include src/pysolarcloud/data/*.json" in manifest


def test_load_is_cached(catalog):
    assert load_measure_points() is catalog


def test_public_exports():
    for name in ("MeasurePoint", "MeasurePointCatalog", "load_measure_points"):
        assert hasattr(pysolarcloud, name)


# --- Conversion fidelity (pinned against sungrow-hass measure_points_data.py) --


def test_counts_match_the_integration_source(catalog):
    assert len(catalog.points) == 639  # RAW_POINTS rows (IDs unique)
    assert {name: len(table) for name, table in catalog.enums.items()} == {
        "charger_status": 9,
        "operating_status": 33,
        "microinverter_status": 15,
    }
    assert sorted(p.point_id for p in catalog.points.values() if p.enum) == ["13146", "29", "33716", "51301"]
    assert len(catalog.codes) == 74  # the former literal Plants.measure_points map
    assert sum(1 for p in catalog.points.values() if p.unit is None) == 106


def test_rows_per_documentation_page(catalog):
    pages = Counter(page for point in catalog.points.values() for page in point.catalogs)
    assert pages == {
        "common-inverter-measuring-points": 168,
        "common-energy-storage-inverter-measuring-points": 109,
        "common-plant-measuring-points": 73,
        "common-environment-monitoring-device-measuring-points": 55,
        "common-energy-meter-measuring-points": 41,
        "common-microinverter-measuring-points": 41,
        "common-battery-measuring-points": 35,
        "common-combiner-box-measuring-points": 28,
        "common-communications-device-measuring-points": 20,
        "common-charger-measuring-points": 13,
        "common-ems-device-measuring-points": 12,
        "common-pcs-device-measuring-points": 10,
        "common-lc-device-measuring-points": 10,
        "common-cmu-device-measuring-points": 9,
        "common-bsc-device-measuring-points": 8,
        "common-ihomemanage-measuring-points": 6,
        "common-communications-module-measuring-points": 3,
    }


@pytest.mark.parametrize(
    ("point_id", "name", "unit", "page"),
    [
        ("58601", "Battery Voltage", "V", "common-battery-measuring-points"),
        ("58604", "Battery Level", None, "common-battery-measuring-points"),
        ("83012", "P-radiation-H", "W/m²", "common-plant-measuring-points"),
        ("83016", "Plant Ambient Temperature", "°C", "common-plant-measuring-points"),
        ("83237", "Total field energy storage maximum reactive power", "W", "common-plant-measuring-points"),
        ("33716", "Charging Status", None, "common-charger-measuring-points"),
        ("88035", "Channel 1 Total Feed-in Energy", "Wh", "common-ihomemanage-measuring-points"),
    ],
)
def test_spot_check_rows(catalog, point_id, name, unit, page):
    point = catalog.get(point_id)
    assert point is not None
    assert (point.name, point.unit, point.catalogs) == (name, unit, (page,))


def test_point_83124_is_total_load_consumption(catalog):
    """The plants.py code map and the documented row are now one record."""
    point = catalog.get(83124)
    assert point == MeasurePoint(
        point_id="83124",
        name="Total Load Consumption",
        unit="Wh",
        catalogs=("common-plant-measuring-points",),
        code="total_load_consumption",
    )
    assert catalog.by_code("total_load_consumption") is point


def test_points_absent_from_the_docs_list_no_page_and_say_why(catalog):
    undocumented = {p.point_id: p for p in catalog.points.values() if not p.catalogs}
    assert sorted(undocumented) == ["83123", "83202"]
    assert (undocumented["83202"].name, undocumented["83202"].unit) == ("Nominal Power", "Wp")
    for point in undocumented.values():
        assert point.note is not None
        assert point.note.startswith("Not on the iSolarCloud docs pages.")
    assert all(p.note is None for p in catalog.points.values() if p.catalogs)


def test_cmu_bsc_shared_cell_points_list_both_pages(catalog):
    for point_id in ("59008", "59010", "59012", "59014"):
        assert catalog.points[point_id].catalogs == (
            "common-cmu-device-measuring-points",
            "common-bsc-device-measuring-points",
        )


def test_inverter_and_storage_inverter_share_the_operating_status_table(catalog):
    assert catalog.points["29"].enum == catalog.points["13146"].enum == "operating_status"
    assert catalog.enum_for("29") is catalog.enum_for("13146")


# --- Plants.measure_points is a view of the catalog ---------------------------


def test_plants_measure_points_come_from_the_catalog(catalog):
    assert Plants.measure_points == dict(catalog.codes)
    assert list(Plants.measure_points)[:3] == ["83022", "83024", "83033"]  # request order kept
    assert Plants.measure_points["83124"] == "total_load_consumption"


def test_every_documented_code_has_a_row_except_the_undocumented_one(catalog):
    undocumented = [pid for pid in catalog.codes if pid not in catalog.points]
    # 83335 was already requested by the library but is not on the plant docs page.
    assert undocumented == ["83335"]
    assert catalog.point_id_for_code("energy_storage_remaining_charge_ems") == "83335"
    assert catalog.by_code("energy_storage_remaining_charge_ems") is None
    for point_id, code in catalog.codes.items():
        if point_id in catalog.points:
            assert catalog.points[point_id].code == code


# --- Lookups and enum decoding -------------------------------------------------


def test_lookups(catalog):
    assert catalog.get("nope") is None
    assert catalog.by_code("nope") is None
    assert catalog.point_id_for_code("nope") is None
    assert catalog.resolve("83124") is catalog.resolve(83124) is catalog.resolve("total_load_consumption")
    assert catalog.resolve("missing_code") is None


@pytest.mark.parametrize(
    ("value", "label"),
    [(3, "Charging"), ("3", "Charging"), ("3.0", "Charging"), (3.0, "Charging"), (99, None), ("x", None), (None, None)],
)
def test_decode_enum(catalog, value, label):
    assert catalog.decode_enum("33716", value) == label


def test_decode_enum_on_non_enum_or_unknown_point(catalog):
    assert catalog.decode_enum("58601", 1) is None
    assert catalog.decode_enum("00000", 1) is None
    assert catalog.enum_options("58601") is None
    assert catalog.decode_enum("33716", float("inf")) is None


def test_enum_options_are_deduplicated_in_table_order(catalog):
    options = catalog.enum_options("29")
    assert options is not None
    assert options[0] == "Grid-connected operation"
    assert options.count("Derated running") == 1  # codes 128 and 33024 share the label
    assert len(options) == 32
    assert catalog.decode_enum(13146, 33024) == "Derated running"
    assert catalog.enum_options("51301") == tuple(catalog.enums["microinverter_status"].values())


def test_catalog_is_immutable(catalog):
    point = catalog.points["58601"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        point.name = "x"  # type: ignore[misc]
    with pytest.raises(TypeError):
        catalog.points["1"] = point  # type: ignore[index]
    with pytest.raises(TypeError):
        catalog.enums["charger_status"][1] = "x"  # type: ignore[index]


# --- Document validation -------------------------------------------------------


def _minimal(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "enums": {"status": {"1": "On"}},
        "points": [{"id": "1", "name": "A", "unit": None, "catalogs": ["p"], "enum": "status"}],
        "codes": {"1": "a"},
    }
    document.update(overrides)
    return document


def test_parse_minimal_document():
    parsed = _parse(_minimal())
    assert parsed.decode_enum("1", 1) == "On"
    assert parsed.by_code("a") == parsed.points["1"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"schema_version": 2}, "schema_version"),
        ({"schema_version": None}, "schema_version"),
        (
            {"points": [{"id": "1", "name": "A", "catalogs": []}, {"id": "1", "name": "B", "catalogs": []}]},
            "twice",
        ),
        ({"points": [{"id": "1", "name": "A", "catalogs": [], "enum": "missing"}]}, "unknown enum"),
        ({"codes": {"1": "a", "2": "a"}}, "same code"),
    ],
)
def test_parse_rejects_malformed_documents(overrides, message):
    with pytest.raises(ValueError, match=message):
        _parse(_minimal(**overrides))
