"""The iSolarCloud measure-point catalog, shipped as package data.

The documented iSolarCloud measuring points (the ``common-*-measuring-points`` pages of
the OpenAPI docs), their value-enum tables and this library's readable point codes live
in ``pysolarcloud/data/measure_points.json``: one schema-versioned JSON document that is
the single source of truth for every consumer. :class:`~pysolarcloud.plants.Plants`
builds its default ``measure_points`` request map from it, and the
`sungrow-hass <https://github.com/KRoperUK/sungrow-hass>`_ integration layers its Home
Assistant naming and classification on top of it.

Only vendor facts and library facts are in the catalog — the point ID, the documented
English name and unit, the documentation page(s) listing the point, the documented enum
table and the library's own readable ``code``. Presentation (display names, Home
Assistant device/state classes, icons) belongs to the consumer.

.. code-block:: python

    from pysolarcloud import load_measure_points

    catalog = load_measure_points()
    point = catalog.by_code("total_load_consumption")
    assert (point.point_id, point.name, point.unit) == ("83124", "Total Load Consumption", "Wh")
    assert catalog.decode_enum("33716", "3") == "Charging"

The document is parsed once and cached; the returned objects are immutable.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files
from types import MappingProxyType
from typing import Any

__all__ = ["SCHEMA_VERSION", "MeasurePoint", "MeasurePointCatalog", "load_measure_points"]

#: The ``schema_version`` of ``measure_points.json`` this module understands. Bumped on
#: any incompatible change to the document's shape, so a mismatched data file fails
#: loudly instead of being misread.
SCHEMA_VERSION = 1

_DATA_PACKAGE = "pysolarcloud"
_DATA_PATH = ("data", "measure_points.json")


@dataclass(frozen=True, slots=True)
class MeasurePoint:
    """One documented iSolarCloud measuring point."""

    #: Numeric point ID as the API spells it (a string, e.g. ``"83124"``).
    point_id: str
    #: English name from the iSolarCloud measuring-point docs.
    name: str
    #: Documented unit, or ``None`` when the docs list none (dimensionless or textual
    #: points). The API's per-response ``point_unit`` remains authoritative at runtime.
    unit: str | None
    #: Slugs of the documentation pages listing this point (e.g.
    #: ``"common-plant-measuring-points"``). Most points appear on exactly one page; it
    #: is empty for a point observed in API responses but listed on no page (see
    #: :attr:`note`).
    catalogs: tuple[str, ...]
    #: Name of the value-enum table in :attr:`MeasurePointCatalog.enums`, or ``None``.
    enum: str | None = None
    #: This library's readable code for the point (the key used by
    #: :meth:`~pysolarcloud.plants.Plants.async_get_realtime_data`), or ``None`` when
    #: the library does not request the point by default.
    code: str | None = None
    #: Provenance remark for a point that needs one (e.g. why it is not on a docs page).
    note: str | None = None


@dataclass(frozen=True, slots=True)
class MeasurePointCatalog:
    """The loaded measure-point catalog. Obtain it with :func:`load_measure_points`."""

    schema_version: int
    #: Documented points keyed by point ID, in document order.
    points: Mapping[str, MeasurePoint]
    #: Value-enum tables keyed by table name: ``{raw integer code: label}``.
    enums: Mapping[str, Mapping[int, str]]
    #: The library's default point map, ``{point_id: code}``, in request order. Also
    #: covers points the library requests although the docs do not list them, which
    #: therefore have no entry in :attr:`points`.
    codes: Mapping[str, str]
    _point_ids_by_code: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)

    def get(self, point_id: str | int) -> MeasurePoint | None:
        """Return the documented point with this ID, else ``None``."""
        return self.points.get(str(point_id))

    def point_id_for_code(self, code: str) -> str | None:
        """Return the point ID for a library code (``"total_load_consumption"`` -> ``"83124"``)."""
        return self._point_ids_by_code.get(code)

    def by_code(self, code: str) -> MeasurePoint | None:
        """Return the documented point for a library code, else ``None``."""
        point_id = self.point_id_for_code(code)
        return None if point_id is None else self.points.get(point_id)

    def resolve(self, key: str | int) -> MeasurePoint | None:
        """Return the documented point for a point ID *or* a library code, else ``None``."""
        key = str(key)
        return self.get(key) if key.isdigit() else self.by_code(key)

    def enum_for(self, point_id: str | int) -> Mapping[int, str] | None:
        """Return the value-enum table for a point, else ``None``."""
        point = self.get(point_id)
        if point is None or point.enum is None:
            return None
        return self.enums[point.enum]

    def enum_options(self, point_id: str | int) -> tuple[str, ...] | None:
        """Return the distinct labels of a point's enum table in table order, else ``None``.

        Labels are de-duplicated: some documented tables give two codes the same label.
        """
        table = self.enum_for(point_id)
        return None if table is None else tuple(dict.fromkeys(table.values()))

    def decode_enum(self, point_id: str | int, value: Any) -> str | None:
        """Map a raw enum value to its documented label.

        ``value`` may be an int, a float or a numeric string as the API returns it
        (``"3"``, ``"3.0"``). Returns ``None`` when the point has no enum table, the value
        is not numeric, or the code is not in the table — never an undocumented label.
        """
        table = self.enum_for(point_id)
        if table is None:
            return None
        try:
            raw = int(float(value))
        except (TypeError, ValueError, OverflowError):
            return None
        return table.get(raw)


def _parse(document: Mapping[str, Any]) -> MeasurePointCatalog:
    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(f"measure_points.json schema_version {version!r} is not supported (expected {SCHEMA_VERSION})")
    enums = MappingProxyType(
        {
            name: MappingProxyType({int(raw): str(label) for raw, label in table.items()})
            for name, table in document["enums"].items()
        }
    )
    codes: dict[str, str] = {str(pid): str(code) for pid, code in document["codes"].items()}
    points: dict[str, MeasurePoint] = {}
    for row in document["points"]:
        point_id = str(row["id"])
        if point_id in points:
            raise ValueError(f"measure_points.json lists point {point_id} twice")
        enum = row.get("enum")
        if enum is not None and enum not in enums:
            raise ValueError(f"measure_points.json point {point_id} references unknown enum {enum!r}")
        points[point_id] = MeasurePoint(
            point_id=point_id,
            name=str(row["name"]),
            unit=row.get("unit"),
            catalogs=tuple(row["catalogs"]),
            enum=enum,
            code=codes.get(point_id),
            note=row.get("note"),
        )
    point_ids_by_code = {code: point_id for point_id, code in codes.items()}
    if len(point_ids_by_code) != len(codes):
        raise ValueError("measure_points.json maps two points to the same code")
    return MeasurePointCatalog(
        schema_version=version,
        points=MappingProxyType(points),
        enums=enums,
        codes=MappingProxyType(codes),
        _point_ids_by_code=MappingProxyType(point_ids_by_code),
    )


@cache
def load_measure_points() -> MeasurePointCatalog:
    """Load (once) and return the packaged measure-point catalog."""
    resource = files(_DATA_PACKAGE).joinpath(*_DATA_PATH)
    return _parse(json.loads(resource.read_text(encoding="utf-8")))
