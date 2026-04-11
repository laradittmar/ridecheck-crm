from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from app.scripts.seed_viaticos_zones import DEFAULT_CSV_PATH, DEFAULT_XLSX_PATH, _read_zone_rows


def sync_viaticos_csv(source_path: Path, output_path: Path) -> int:
    rows = _read_zone_rows(source_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["zone_group", "zone_detail", "viaticos"])
        for row in rows:
            writer.writerow([row.zone_group, row.zone_detail, row.viaticos])
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh app/data/viaticos_zones.csv from the XLSX workbook")
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_XLSX_PATH,
        help=f"Path to the pricing workbook (default: {DEFAULT_XLSX_PATH})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help=f"CSV output path (default: {DEFAULT_CSV_PATH})",
    )
    args = parser.parse_args(argv)

    source_path = args.source.expanduser().resolve()
    output_path = args.output.expanduser().resolve()

    if not source_path.exists():
        print(f"Workbook not found: {source_path}", file=sys.stderr)
        return 1

    written = sync_viaticos_csv(source_path, output_path)
    print(f"Wrote {written} rows to {output_path} from {source_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
