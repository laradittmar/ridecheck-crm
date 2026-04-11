from __future__ import annotations

import argparse
import csv
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

DEFAULT_XLSX_PATH = (
    Path(__file__).resolve().parents[3]
    / "Pricing - tabla de viaticos"
    / "Zonas - viaticos Actualizado xls.xlsx"
)
DEFAULT_CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "viaticos_zones.csv"


@dataclass(frozen=True)
class ZoneRow:
    zone_group: str
    zone_detail: str
    viaticos: int


def _sheet_xml_path(zip_file: zipfile.ZipFile) -> str:
    workbook_root = ET.fromstring(zip_file.read("xl/workbook.xml"))
    first_sheet = workbook_root.find("x:sheets/x:sheet", NS)
    if first_sheet is None:
        raise RuntimeError("Workbook has no sheets")

    rel_id = first_sheet.attrib.get(f"{{{DOC_REL_NS}}}id")
    if not rel_id:
        raise RuntimeError("Workbook sheet is missing relationship id")

    rels_root = ET.fromstring(zip_file.read("xl/_rels/workbook.xml.rels"))
    for rel in rels_root.findall("r:Relationship", REL_NS):
        if rel.attrib.get("Id") == rel_id:
            target = rel.attrib.get("Target", "")
            if not target:
                break
            normalized = target.lstrip("/")
            if not normalized.startswith("xl/"):
                normalized = f"xl/{normalized}"
            return normalized

    raise RuntimeError("Could not resolve first worksheet path")


def _shared_strings(zip_file: zipfile.ZipFile) -> list[str]:
    try:
        raw = zip_file.read("xl/sharedStrings.xml")
    except KeyError:
        return []

    root = ET.fromstring(raw)
    values: list[str] = []
    for item in root.findall("x:si", NS):
        parts = [node.text or "" for node in item.findall(".//x:t", NS)]
        values.append("".join(parts))
    return values


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", NS)).strip()

    value_node = cell.find("x:v", NS)
    if value_node is None or value_node.text is None:
        return ""

    raw = value_node.text.strip()
    if cell_type == "s":
        index = int(raw)
        return shared[index].strip() if 0 <= index < len(shared) else raw
    return raw


def _read_zone_rows(xlsx_path: Path) -> list[ZoneRow]:
    with zipfile.ZipFile(xlsx_path) as zip_file:
        shared = _shared_strings(zip_file)
        sheet_path = _sheet_xml_path(zip_file)
        sheet_root = ET.fromstring(zip_file.read(sheet_path))

    rows: list[dict[str, str]] = []
    for row in sheet_root.findall(".//x:sheetData/x:row", NS):
        values: dict[str, str] = {}
        for cell in row.findall("x:c", NS):
            ref = cell.attrib.get("r", "")
            column = "".join(ch for ch in ref if ch.isalpha())
            if not column:
                continue
            values[column] = _cell_value(cell, shared)
        rows.append(values)

    if len(rows) < 2:
        raise RuntimeError("Workbook does not contain header + data rows")

    header = rows[1]
    expected = {
        "A": "Zona_Grupo",
        "B": "Zona_detalle",
        "C": "Precio Viaticos",
    }
    for col, expected_name in expected.items():
        actual = (header.get(col) or "").strip()
        if actual != expected_name:
            raise RuntimeError(
                f"Unexpected header in column {col}: expected {expected_name!r}, got {actual!r}"
            )

    zone_rows: list[ZoneRow] = []
    for index, raw in enumerate(rows[2:], start=3):
        zone_group = (raw.get("A") or "").strip()
        zone_detail = (raw.get("B") or "").strip()
        viaticos_raw = (raw.get("C") or "").strip()

        if not zone_group and not zone_detail and not viaticos_raw:
            continue
        if not zone_group or not zone_detail or not viaticos_raw:
            raise RuntimeError(f"Row {index} is incomplete: {raw}")
        if not viaticos_raw.isdigit():
            raise RuntimeError(f"Row {index} has non-numeric viaticos: {viaticos_raw!r}")

        zone_rows.append(
            ZoneRow(
                zone_group=zone_group,
                zone_detail=zone_detail,
                viaticos=int(viaticos_raw),
            )
        )

    if not zone_rows:
        raise RuntimeError("Workbook produced zero zone rows")

    unique_keys = {(row.zone_group.casefold(), row.zone_detail.casefold()) for row in zone_rows}
    if len(unique_keys) != len(zone_rows):
        raise RuntimeError("Workbook contains duplicate zone_group/zone_detail pairs")

    return zone_rows


def _read_zone_rows_from_csv(csv_path: Path) -> list[ZoneRow]:
    rows: list[ZoneRow] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = ["zone_group", "zone_detail", "viaticos"]
        if list(reader.fieldnames or []) != expected:
            raise RuntimeError(
                f"Unexpected CSV headers: expected {expected!r}, got {reader.fieldnames!r}"
            )
        for index, raw in enumerate(reader, start=2):
            zone_group = (raw.get("zone_group") or "").strip()
            zone_detail = (raw.get("zone_detail") or "").strip()
            viaticos_raw = (raw.get("viaticos") or "").strip()
            if not zone_group or not zone_detail or not viaticos_raw:
                raise RuntimeError(f"CSV row {index} is incomplete: {raw}")
            if not viaticos_raw.isdigit():
                raise RuntimeError(f"CSV row {index} has non-numeric viaticos: {viaticos_raw!r}")
            rows.append(
                ZoneRow(
                    zone_group=zone_group,
                    zone_detail=zone_detail,
                    viaticos=int(viaticos_raw),
                )
            )
    if not rows:
        raise RuntimeError("CSV produced zero zone rows")
    unique_keys = {(row.zone_group.casefold(), row.zone_detail.casefold()) for row in rows}
    if len(unique_keys) != len(rows):
        raise RuntimeError("CSV contains duplicate zone_group/zone_detail pairs")
    return rows


def _load_zone_rows(source_path: Path) -> list[ZoneRow]:
    suffix = source_path.suffix.lower()
    if suffix == ".csv":
        return _read_zone_rows_from_csv(source_path)
    if suffix == ".xlsx":
        return _read_zone_rows(source_path)
    raise RuntimeError(f"Unsupported source format: {source_path}")


def seed_viaticos_zones(source_path: Path) -> int:
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models import ViaticosZone

    zone_rows = _load_zone_rows(source_path)
    db = SessionLocal()
    try:
        db.execute(delete(ViaticosZone))
        db.add_all(
            [
                ViaticosZone(
                    zone_group=row.zone_group,
                    zone_detail=row.zone_detail,
                    viaticos=row.viaticos,
                )
                for row in zone_rows
            ]
        )
        db.commit()
        return len(zone_rows)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed viaticos_zones from the pricing XLSX workbook")
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_CSV_PATH if DEFAULT_CSV_PATH.exists() else DEFAULT_XLSX_PATH,
        help=f"Path to the pricing source (.csv or .xlsx). Defaults to {DEFAULT_CSV_PATH} when present, otherwise {DEFAULT_XLSX_PATH}",
    )
    args = parser.parse_args(argv)

    source_path = args.source.expanduser().resolve()
    if not source_path.exists():
        print(f"Pricing source not found: {source_path}", file=sys.stderr)
        return 1

    inserted = seed_viaticos_zones(source_path)
    print(f"Seeded {inserted} viaticos_zones rows from {source_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
