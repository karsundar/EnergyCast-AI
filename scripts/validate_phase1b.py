#!/usr/bin/env python3
"""Generate the bounded Phase 1B inventory and preliminary quality report."""

from __future__ import annotations

import hashlib
import csv
import json
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

import openpyxl
import xlrd

from download_datasets import load_manifest


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
LEDGER = ROOT / "reports" / "acquisition_phase1b.json"
INVENTORY = ROOT / "reports" / "data_inventory.json"
QUALITY = ROOT / "reports" / "data_quality_preliminary.md"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_missing(value: Any) -> bool:
    return value is None or str(value).strip() in {"", ".", "NA", "N/A", "nan"}


def value_type(value: Any) -> str:
    if is_missing(value):
        return "missing"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, datetime):
        return "datetime"
    return "string"


def unique_names(values: Iterable[Any]) -> list[str]:
    counts: Counter[str] = Counter()
    names: list[str] = []
    for index, value in enumerate(values, 1):
        base = str(value).strip() if not is_missing(value) else f"column_{index}"
        counts[base] += 1
        names.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return names


def summarize_rows(columns: list[str], rows: list[list[Any]], keys: list[str]) -> dict[str, Any]:
    missing = {column: 0 for column in columns}
    types = {column: set() for column in columns}
    indices = {column: index for index, column in enumerate(columns)}
    seen: Counter[tuple[Any, ...]] = Counter()
    for row in rows:
        padded = row + [None] * (len(columns) - len(row))
        for index, column in enumerate(columns):
            value = padded[index]
            if is_missing(value):
                missing[column] += 1
            else:
                types[column].add(value_type(value))
        if keys:
            seen[tuple(padded[indices[key]] for key in keys)] += 1
    return {
        "row_count": len(rows),
        "column_definitions": [
            {"name": column, "types": sorted(types[column]), "missing": missing[column]}
            for column in columns
        ],
        "missing_values": missing,
        "duplicate_observations": sum(count - 1 for count in seen.values() if count > 1),
    }


def parse_hour(value: Any, datemode: int | None = None) -> datetime:
    if isinstance(value, (int, float)) and datemode is not None:
        return xlrd.xldate_as_datetime(value, datemode)
    text = str(value).strip()
    date_text, time_text = text.split()[:2]
    hour, minute = (int(part) for part in time_text.split(":")[:2])
    parsed = datetime.strptime(date_text, "%m/%d/%Y")
    return parsed + timedelta(days=1) if hour == 24 else parsed.replace(hour=hour, minute=minute)


def read_ercot(path: Path) -> tuple[list[str], list[list[Any]], list[datetime]]:
    if path.suffix.lower() == ".xls":
        book = xlrd.open_workbook(path, on_demand=True)
        sheet = book.sheet_by_index(0)
        columns = unique_names(sheet.row_values(0))
        raw_rows = [sheet.row_values(index) for index in range(1, sheet.nrows)]
        timestamps = [parse_hour(row[0], book.datemode) for row in raw_rows]
    else:
        with zipfile.ZipFile(path) as archive:
            member = next(name for name in archive.namelist() if name.lower().endswith(".xlsx"))
            book = openpyxl.load_workbook(BytesIO(archive.read(member)), read_only=True, data_only=True)
            sheet = book.worksheets[0]
            values = list(sheet.iter_rows(values_only=True))
        columns = unique_names(values[0])
        raw_rows = [list(row) for row in values[1:] if not is_missing(row[0])]
        timestamps = [parse_hour(row[0]) for row in raw_rows]
    rows = [[timestamp.isoformat(), *row[1:]] for timestamp, row in zip(timestamps, raw_rows)]
    columns[0] = "hour_ending"
    coverage_dates = [timestamp - timedelta(microseconds=1) for timestamp in timestamps]
    return columns, rows, coverage_dates


def eia_columns(header_rows: list[tuple[Any, ...]]) -> list[str]:
    sector = ""
    measure = ""
    names: list[str] = []
    for top, middle, unit in zip(*header_rows):
        if not is_missing(top):
            sector = str(top).strip()
        if not is_missing(middle):
            measure = str(middle).strip()
        if str(unit).strip() in {"Year", "Month", "MONTH", "State", "Data Status"}:
            names.append(str(unit).strip().lower().replace(" ", "_"))
        else:
            names.append("_".join(part for part in (sector, measure, str(unit).strip()) if part))
    return unique_names(names)


def read_eia(path: Path) -> tuple[list[str], list[list[Any]], list[datetime], str]:
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet_name = "Monthly" if "Monthly" in book.sheetnames else "Monthly-States"
    sheet = book[sheet_name]
    values = list(sheet.iter_rows(values_only=True))
    columns = eia_columns(values[:3])
    rows = [
        list(row) for row in values[3:]
        if isinstance(row[0], (int, float)) and isinstance(row[1], (int, float))
    ]
    dates = [datetime(int(row[0]), int(row[1]), 1) for row in rows]
    return columns, rows, dates, sheet_name


def read_gpr(path: Path) -> tuple[list[str], list[list[Any]], list[datetime]]:
    book = xlrd.open_workbook(path, on_demand=True)
    sheet = book.sheet_by_index(0)
    columns = unique_names(sheet.row_values(0))
    rows = [sheet.row_values(index) for index in range(1, sheet.nrows) if not is_missing(sheet.cell_value(index, 0))]
    dates = [xlrd.xldate_as_datetime(row[0], book.datemode) for row in rows]
    rows = [[date_value.date().isoformat(), *row[1:]] for date_value, row in zip(dates, rows)]
    return columns, rows, dates


def read_forecast(path: Path) -> tuple[list[str], list[list[Any]], list[datetime]]:
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = book.worksheets[0]
    values = list(sheet.iter_rows(values_only=True))
    columns = ["year", "month", "monthly_peak", "monthly_energy"]
    rows = [list(row[:4]) for row in values[2:] if isinstance(row[0], (int, float)) and isinstance(row[1], (int, float))]
    dates = [datetime(int(row[0]), int(row[1]), 1) for row in rows]
    return columns, rows, dates


def read_fred(path: Path) -> tuple[list[str], list[list[Any]], list[datetime]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        columns = unique_names(next(reader))
        rows = [row for row in reader if row and not is_missing(row[0])]
    dates = [datetime.fromisoformat(row[0]) for row in rows]
    return columns, rows, dates


def read_gscpi(path: Path) -> tuple[list[str], list[list[Any]], list[datetime]]:
    book = xlrd.open_workbook(path, on_demand=True)
    sheet = book.sheet_by_name("GSCPI Monthly Data")
    columns = unique_names(sheet.row_values(0))
    rows = [sheet.row_values(index) for index in range(1, sheet.nrows) if sheet.cell_value(index, 0)]
    dates = [datetime.strptime(str(row[0]), "%d-%b-%Y") for row in rows]
    rows = [[date_value.date().isoformat(), *row[1:]] for date_value, row in zip(dates, rows)]
    return columns, rows, dates


def source_records() -> dict[tuple[str, str], dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for ledger_path in sorted((ROOT / "reports").glob("acquisition*.json")):
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        for record in ledger.get("datasets", []):
            if record.get("path"):
                records[(str(record["id"]), Path(record["path"]).name)] = record
    return records


def inventory_record(path: Path, source: dict[str, Any]) -> dict[str, Any]:
    dataset_id = path.parent.name
    issues: list[str] = []
    if dataset_id == "fred_global_supply_chain_pressure":
        columns, rows, dates = read_gscpi(path)
        summary = summarize_rows(columns, rows, ["Date"])
        units = {"Date": "month", "GSCPI": "standard deviations from historical average"}
        geography = ["Global"]
        issues.append("official New York Fed workbook used because the FRED CSV endpoint was unavailable")
    elif dataset_id.startswith("fred_"):
        columns, rows, dates = read_fred(path)
        summary = summarize_rows(columns, rows, [columns[0]])
        units = {columns[0]: "date", columns[1]: "series-specific unit; not embedded in FRED CSV"}
        manifest_entry = next(dataset for dataset in load_manifest(ROOT / "config" / "datasets.json") if dataset["id"] == dataset_id)
        geography = [manifest_entry["target_geography"]]
        issues.append("series unit and seasonal-adjustment metadata are not embedded; retain FRED series metadata during canonical ingestion")
    elif dataset_id == "ercot_hourly_load":
        columns, rows, dates = read_ercot(path)
        summary = summarize_rows(columns, rows, ["hour_ending"])
        units = {column: ("timestamp, Central prevailing time" if column == "hour_ending" else "MW") for column in columns}
        geography = [column for column in columns if column != "hour_ending"]
        expected = 24 * len({date_value.date() for date_value in dates})
        if len(rows) != expected:
            issues.append(f"row count {len(rows)} differs from 24 per represented date ({expected}); inspect DST/hour-ending convention")
        if summary["duplicate_observations"]:
            issues.append(f"{summary['duplicate_observations']} repeated local hour-ending label(s) reflect DST fall-back")
    elif dataset_id == "eia_monthly_electricity_sales":
        columns, rows, dates, sheet = read_eia(path)
        summary = summarize_rows(columns, rows, ["year", "month", "state"])
        units = {column: next((unit for unit in ("Thousand Dollars", "Megawatthours", "Count", "Cents/kWh") if unit in column), "identifier") for column in columns}
        geography = sorted({str(row[2]) for row in rows})
        issues.append(f"inventory uses primary sheet {sheet}; YTD and territory sheets are retained but not counted")
    elif dataset_id == "global_geopolitical_risk":
        columns, rows, dates = read_gpr(path)
        summary = summarize_rows(columns, rows, ["month"])
        units = {column: ("month" if column == "month" else "index/share/count; see var_label metadata") for column in columns}
        geography = [column.removeprefix("GPRC_") for column in columns if column.startswith("GPRC_")]
    else:
        columns, rows, dates = read_forecast(path)
        summary = summarize_rows(columns, rows, ["year", "month"])
        units = {"year": "year", "month": "month", "monthly_peak": "MW (inferred; unit not embedded)", "monthly_energy": "MWh (inferred; unit not embedded)"}
        geography = ["ERCOT"]
        issues.append("peak and energy units are inferred from ERCOT context; workbook cells do not embed units")
    publication = source.get("publication_date")
    delay = None
    if publication:
        delay = (datetime.fromisoformat(publication) - max(dates)).days
    status = "warning" if issues else "pass"
    return {
        "dataset_id": dataset_id,
        "source_url": source["url"],
        "local_path": str(path.relative_to(ROOT)),
        "file_size": path.stat().st_size,
        "sha256": sha256(path),
        **summary,
        "actual_start": min(dates).date().isoformat(),
        "actual_end": max(dates).date().isoformat(),
        "units": units,
        "geographic_identifiers": geography,
        "time_zone": "Central prevailing time; DST requires explicit normalization" if dataset_id == "ercot_hourly_load" else None,
        "publication_date": publication,
        "reporting_delay_days": delay,
        "validation_status": status,
        "issues": issues,
    }


def main() -> int:
    sources = source_records()
    records = [
        inventory_record(path, sources[(path.parent.name, path.name)])
        for path in sorted(RAW.rglob("*")) if path.is_file()
    ]
    manifest = load_manifest(ROOT / "config" / "datasets.json")
    for dataset in manifest:
        if dataset.get("source_handler") in {"fred", "new_york_fed"} and not any(record["dataset_id"] == dataset["id"] for record in records):
            records.append({
                "dataset_id": dataset["id"], "source_url": dataset["url"], "local_path": None,
                "file_size": None, "sha256": None, "row_count": None, "actual_start": None,
                "actual_end": None, "column_definitions": [], "missing_values": {},
                "duplicate_observations": None, "validation_status": "blocked",
                "issues": ["official FRED endpoint timed out without returning response bytes"],
            })
    output = {"created_at": datetime.now(timezone.utc).isoformat(), "phase": "1B", "artifacts": records}
    INVENTORY.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")

    acquired = [record for record in records if record["local_path"]]
    blocked = [record for record in records if record["validation_status"] == "blocked"]
    ercot_schemas = {Path(record["local_path"]).name: [column["name"] for column in record["column_definitions"]] for record in acquired if record["dataset_id"] == "ercot_hourly_load"}
    artifact_rows = [
        f"| {Path(record['local_path']).name} | {record['row_count']} | {record['actual_start']} | {record['actual_end']} | {record['duplicate_observations']} | {record['validation_status']} |"
        for record in acquired
    ]
    report = [
        "# Preliminary data-quality report", "", f"Generated: {output['created_at']}", "",
        "## Result", "", f"- Acquired and inventoried {len(acquired)} bounded artifacts.",
        f"- Blocked: {len(blocked)} FRED artifacts because agent-triggered requests to the official host returned no bytes before timeout.",
        "- Copernicus remained disabled; no climate data was requested.", "", "## Observations", "",
        "- ERCOT 2004 uses `Hour_End` and legacy zone labels (`FAR_WEST`, `NORTH_C`, `SOUTHERN`, `SOUTH_C`); 2020+ uses `Hour Ending`/`HourEnding` and (`FWEST`, `NCENT`, `SOUTH`, `SCENT`). Values are MW.",
        "- ERCOT timestamps are hour-ending Central prevailing time. DST normalization remains required before merging years.",
        "- EIA 1990–2009 includes an `OTHER` sector absent from the current workbook; the current consolidated workbook covers 2010 through its reported current month.",
        "- EIA units are embedded in the three-row header: thousand dollars, MWh, customer count, and cents/kWh. Current-period rows are preliminary while older rows are final.",
        "- GPR is monthly and contains global and country index families plus variable-label metadata. Missing early values reflect series availability and were not imputed.",
        "- The selected ERCOT forecast is the explicitly named monthly peak-demand and energy workbook published 2025-04-08. Its unit labels are not embedded and remain a documentation warning.",
        "- Manually imported FRED CSVs are validated for header, date coverage, missing values, and duplicate dates; units and seasonal-adjustment metadata require the FRED series metadata page.",
        "- ERCOT URL publication dates identify initial catalog publication. For rolling annual files, they can precede the final observation and are not equivalent to the last update time.",
        "", "## Artifact validation summary", "",
        "| Artifact | Rows | Start | End | Duplicate keys | Status |",
        "|---|---:|---|---|---:|---|", *artifact_rows,
        "", "## ERCOT schema fingerprints", "", "```json", json.dumps(ercot_schemas, indent=2), "```", "",
        "## CDS configuration (do not execute yet)", "",
        "1. Create an account at the Copernicus Climate Data Store and sign in.",
        "2. Open each configured ERA5 and seasonal dataset page and accept its current license/terms.",
        "3. Copy the personal API URL and key into `~/.cdsapirc`; keep that file outside this repository and restrict its permissions.",
        "4. Install only into the isolated project environment: `.venv/bin/python -m pip install cdsapi`.",
        "5. Confirm `cdsapi.Client()` authenticates without printing credentials.",
        "6. Re-open the live dataset forms and validate every variable, area, format, product type, originating centre, seasonal system, initialization month, lead time, and ensemble-member parameter.",
        "7. Set `request_parameters.validated` and `enabled` to true only after a one-month dry-run request plan is reviewed. Keep monthly chunks and the configured U.S. bounds.",
        "8. Validate that seasonal outputs preserve forecast reference time, lead month, and ensemble member before expanding coverage.",
    ]
    QUALITY.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"inventory: {INVENTORY}")
    print(f"quality report: {QUALITY}")
    print(f"acquired={len(acquired)} blocked={len(blocked)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
