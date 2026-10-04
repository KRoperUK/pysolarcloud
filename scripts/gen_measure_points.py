"""One-off migration tool: build ``src/pysolarcloud/data/measure_points.json``.

The measure-point catalog used to live as Python rows in the sungrow-hass integration
(``custom_components/sungrow/measure_points_data.py``) while this library kept its own
``Plants.measure_points`` ``{point_id: code}`` map. KRoperUK/sungrow-hass#484 moves the
catalog here as JSON data; this script performed that conversion so it can be audited
and re-run. It is migration tooling, not part of the package: once the JSON is the
source of truth, edit the JSON directly.

The inputs are parsed with :mod:`ast`, never imported, so neither Home Assistant nor
the integration needs to be installed::

    git -C ../sungrow-hass show <sha>:custom_components/sungrow/measure_points_data.py > /tmp/mpd.py
    git show <sha>:src/pysolarcloud/plants.py > /tmp/plants.py
    python scripts/gen_measure_points.py --points /tmp/mpd.py --points-ref sungrow-hass@<sha> \
        --codes /tmp/plants.py --codes-ref pysolarcloud@<sha>

What is carried over, and what deliberately is not:

* ``RAW_POINTS`` rows -> ``points`` (id, name, unit; a blank unit becomes ``null``). The
  ``# --- Title (slug) ---`` section comment above each row becomes its ``catalogs``,
  except for the two rows (``_UNDOCUMENTED``) that no docs page lists.
* The cloud enum tables referenced by ``ENUM_MAPS`` -> ``enums``, named after their
  variable (``_CHARGER_STATUS`` -> ``charger_status``), with each point's ``enum``
  naming its table. The ``**MODBUS_ENUM_MAPS`` spread is skipped: those tables decode
  the integration's local-Modbus register codes, which are not iSolarCloud points.
* ``Plants.measure_points`` -> ``codes`` (the library's readable codes, kept in order).
* ``CODE_ALIASES`` is *not* carried over: it is Home Assistant display naming (including
  names for the integration's own Modbus and ``getPsDetail`` codes), which stays in the
  integration's override layer.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "src" / "pysolarcloud" / "data" / "measure_points.json"

_SECTION = re.compile(r"^\s*# --- .*\((common-[a-z-]+-measuring-points)\)")

# measure_points_data.py: "CMU/BSC share IDs 59008/59010/59012/59014 (same cell
# voltage/temperature points) — listed once" (under the CMU section). Record both pages.
# Rows the integration added from user-account (app) responses rather than from the docs:
# an audit against the docs pages found them on none of them. They keep their name and
# unit but list no page; their preceding source comment becomes the row's ``note``.
_UNDOCUMENTED = frozenset({"83123", "83202"})

_SHARED_PAGES = {
    pid: ("common-cmu-device-measuring-points", "common-bsc-device-measuring-points")
    for pid in ("59008", "59010", "59012", "59014")
}


def _assignments(tree: ast.Module) -> dict[str, ast.expr]:
    found: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            found[node.target.id] = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            found[node.targets[0].id] = node.value
    return found


def _enum_name(variable: str) -> str:
    return variable.strip("_").lower()


def build(points_source: str, codes_source: str, *, points_ref: str, codes_ref: str) -> dict[str, Any]:
    tree = ast.parse(points_source)
    values = _assignments(tree)
    lines = points_source.splitlines()

    raw_node = values["RAW_POINTS"]
    assert isinstance(raw_node, ast.List)
    enum_node = values["ENUM_MAPS"]
    assert isinstance(enum_node, ast.Dict)

    enums: dict[str, dict[str, str]] = {}
    point_enum: dict[str, str] = {}
    for key, value in zip(enum_node.keys, enum_node.values, strict=True):
        if key is None:  # ``**MODBUS_ENUM_MAPS`` — integration-local, see module docstring.
            continue
        assert isinstance(value, ast.Name), "enum tables are referenced by name"
        name = _enum_name(value.id)
        table = ast.literal_eval(values[value.id])
        enums.setdefault(name, {str(raw): label for raw, label in table.items()})
        point_enum[ast.literal_eval(key)] = name

    points: list[dict[str, Any]] = []
    section: str | None = None
    cursor = raw_node.lineno
    for element in raw_node.elts:
        comment: list[str] = []
        for line in lines[cursor : element.lineno - 1]:
            match = _SECTION.match(line)
            if match:
                section = match.group(1)
                comment = []
            elif line.strip().startswith("#"):
                comment.append(line.strip().lstrip("#").strip())
        cursor = element.lineno
        assert section is not None, f"row on line {element.lineno} precedes any section comment"
        point_id, name, unit = ast.literal_eval(element)
        row: dict[str, Any] = {
            "id": point_id,
            "name": name,
            "unit": unit or None,
            "catalogs": [] if point_id in _UNDOCUMENTED else list(_SHARED_PAGES.get(point_id, (section,))),
        }
        if point_id in _UNDOCUMENTED:
            # Issue references in the comment are sungrow-hass's; qualify them for this repo.
            text = re.sub(r"(?<![\w/])#(\d+)", r"sungrow-hass#\1", " ".join(comment))
            row["note"] = f"Not on the iSolarCloud docs pages. {text}"
        if point_id in point_enum:
            row["enum"] = point_enum[point_id]
        points.append(row)

    missing = set(point_enum) - {row["id"] for row in points}
    assert not missing, f"enum points without a catalog row: {sorted(missing)}"

    codes_values = _assignments(ast.parse(codes_source))
    codes = ast.literal_eval(codes_values["measure_points"])

    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "description": "iSolarCloud OpenAPI common-*-measuring-points docs pages, as transcribed and curated in sungrow-hass",
            "generated_by": "scripts/gen_measure_points.py",
            "points": f"{points_ref}:custom_components/sungrow/measure_points_data.py",
            "codes": f"{codes_ref}:src/pysolarcloud/plants.py (Plants.measure_points)",
        },
        "enums": enums,
        "points": points,
        "codes": codes,
    }


def dump(document: dict[str, Any]) -> str:
    """Serialise with one point / enum value / code per line, so catalog edits diff cleanly."""

    def one(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    def block(items: list[str], brackets: str, indent: str) -> str:
        inner = ",\n".join(f"{indent}  {item}" for item in items)
        return f"{brackets[0]}\n{inner}\n{indent}{brackets[1]}"

    def mapping(values: dict[str, Any], indent: str) -> str:
        return block([f"{one(k)}: {one(v)}" for k, v in values.items()], "{}", indent)

    enums = block([f"{one(name)}: {mapping(table, '    ')}" for name, table in document["enums"].items()], "{}", "  ")
    parts = [
        f'"schema_version": {one(document["schema_version"])}',
        f'"source": {mapping(document["source"], "  ")}',
        f'"enums": {enums}',
        f'"points": {block([one(row) for row in document["points"]], "[]", "  ")}',
        f'"codes": {mapping(document["codes"], "  ")}',
    ]
    text = block(parts, "{}", "") + "\n"
    assert json.loads(text) == document
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--points", type=Path, required=True, help="sungrow-hass measure_points_data.py")
    parser.add_argument("--points-ref", required=True, help="provenance label, e.g. sungrow-hass@<sha>")
    parser.add_argument("--codes", type=Path, required=True, help="pysolarcloud plants.py holding the old map")
    parser.add_argument("--codes-ref", required=True, help="provenance label, e.g. pysolarcloud@<sha>")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    document = build(
        args.points.read_text(encoding="utf-8"),
        args.codes.read_text(encoding="utf-8"),
        points_ref=args.points_ref,
        codes_ref=args.codes_ref,
    )
    args.out.write_text(dump(document), encoding="utf-8")


if __name__ == "__main__":
    main()
