#!/usr/bin/env python3
"""Normalize the approved Phase 1C artifacts and produce quality reports."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import openpyxl
import pandas as pd
import xlrd

from download_datasets import load_manifest
from validate_phase1b import eia_columns, is_missing


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
INVENTORY = REPORTS / "data_inventory.json"
QUALITY_JSON = REPORTS / "data_quality_phase1c.json"
QUALITY_MD = REPORTS / "data_quality_phase1c.md"
CENTRAL = ZoneInfo("America/Chicago")
ZONE_ALIASES = {
    "COAST": "COAST",
    "EAST": "EAST",
    "FAR_WEST": "FAR_WEST",
    "FWEST": "FAR_WEST",
    "NORTH": "NORTH",
    "NORTH_C": "NORTH_CENTRAL",
    "NCENT": "NORTH_CENTRAL",
    "SOUTHERN": "SOUTHERN",
    "SOUTH": "SOUTHERN",
    "SOUTH_C": "SOUTH_CENTRAL",
    "SCENT": "SOUTH_CENTRAL",
    "WEST": "WEST",
}
FRED_UNITS = {
    "fred_industrial_production": "index 2017=100",
    "fred_unemployment_rate": "percent",
    "fred_texas_unemployment_rate": "percent",
    "fred_consumer_price_index": "index 1982-1984=100",
    "fred_real_gdp": "billions of chained 2017 dollars",
    "fred_natural_gas_prices": "US dollars per million Btu",
    "fred_crude_oil_prices": "US dollars per barrel",
    "fred_federal_funds_rate": "percent",
    "fred_texas_population": "thousands of persons",
    "fred_texas_employment": "thousands of persons",
    "fred_economic_policy_uncertainty": "index",
    "fred_global_supply_chain_pressure": "standard deviations from historical average",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_hour_ending(value: Any) -> datetime:
    """Parse ERCOT hour-ending labels without assigning a time zone."""
    if isinstance(value, datetime):
        rounded = value + timedelta(microseconds=500_000)
        return rounded.replace(second=0, microsecond=0)
    text = str(value).strip().replace(" DST", "")
    date_text, time_text = text.split()[:2]
    hour, minute = (int(part) for part in time_text.split(":")[:2])
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
        parsed = datetime.strptime(date_text, "%Y-%m-%d")
    else:
        parsed = datetime.strptime(date_text, "%m/%d/%Y")
    if hour == 24:
        return parsed + timedelta(days=1)
    return parsed.replace(hour=hour, minute=minute)


def _xlsx_rows(payload: bytes) -> tuple[list[Any], list[list[Any]]]:
    book = openpyxl.load_workbook(BytesIO(payload), read_only=True, data_only=True)
    sheet = book.worksheets[0]
    iterator = sheet.iter_rows(values_only=True)
    header = list(next(iterator))
    rows: list[list[Any]] = []
    blank_run = 0
    for values in iterator:
        if is_missing(values[0]):
            blank_run += 1
            if rows and blank_run >= 100:
                break
            continue
        blank_run = 0
        rows.append(list(values[: len(header)]))
    return header, rows


def read_ercot_wide(path: Path) -> tuple[list[str], list[list[Any]]]:
    if path.suffix.lower() == ".xls":
        book = xlrd.open_workbook(path, on_demand=True)
        sheet = book.sheet_by_index(0)
        header = sheet.row_values(0)
        rows = [sheet.row_values(index) for index in range(1, sheet.nrows) if not is_missing(sheet.cell_value(index, 0))]
        for row in rows:
            if isinstance(row[0], (int, float)):
                row[0] = xlrd.xldate_as_datetime(row[0], book.datemode)
    else:
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".xlsx")]
            if len(members) != 1:
                raise ValueError(f"{path.name}: expected one XLSX member, found {len(members)}")
            header, rows = _xlsx_rows(archive.read(members[0]))
    columns = [str(value).strip().upper().replace(" ", "_") for value in header]
    return columns, rows


def localize_hour_endings(values: Iterable[Any]) -> tuple[list[datetime], list[str]]:
    # Convert source hour-ending labels to interval starts before localization.
    # The repeated fall-back interval is labeled HE 02 twice, while 02:00 itself
    # is not an ambiguous wall-clock instant; its 01:00 interval start is.
    naive = [parse_hour_ending(value) - timedelta(hours=1) for value in values]
    totals = Counter(naive)
    seen: Counter[datetime] = Counter()
    aware: list[datetime] = []
    flags: list[str] = []
    for value in naive:
        occurrence = seen[value]
        seen[value] += 1
        fold = min(occurrence, 1) if totals[value] > 1 else 0
        localized = value.replace(tzinfo=CENTRAL, fold=fold)
        round_trip = localized.astimezone(timezone.utc).astimezone(CENTRAL).replace(tzinfo=None)
        flag = "ok"
        if totals[value] > 1:
            flag = "dst_fall_back_first" if occurrence == 0 else "dst_fall_back_second"
        elif round_trip != value:
            # A spring-forward HE 03 becomes a nominal 02:00 interval start.
            # Resolve it to the real preceding hour (01:00 CST) without creating
            # a synthetic interval.
            next_valid = (value + timedelta(hours=1)).replace(tzinfo=CENTRAL)
            localized = (next_valid.astimezone(timezone.utc) - timedelta(hours=1)).astimezone(CENTRAL)
            flag = "dst_spring_forward"
        aware.append(localized)
        flags.append(flag)
    return aware, flags


def normalize_ercot(paths: list[Path]) -> tuple[pd.DataFrame, dict[str, Any]]:
    frames: list[pd.DataFrame] = []
    source_reconciliation: dict[str, Any] = {}
    schemas: dict[str, list[str]] = {}
    for path in sorted(paths):
        columns, rows = read_ercot_wide(path)
        schemas[path.name] = columns
        interval_starts, time_flags = localize_hour_endings(row[0] for row in rows)
        region_indices = {ZONE_ALIASES[column]: index for index, column in enumerate(columns) if column in ZONE_ALIASES}
        if set(region_indices) != set(ZONE_ALIASES.values()):
            missing = sorted(set(ZONE_ALIASES.values()) - set(region_indices))
            raise ValueError(f"{path.name}: missing weather zones: {missing}")
        ercot_index = columns.index("ERCOT") if "ERCOT" in columns else None
        differences: list[float] = []
        records: list[dict[str, Any]] = []
        for row, interval_start_local, time_flag in zip(rows, interval_starts, time_flags):
            interval_start_utc = interval_start_local.astimezone(timezone.utc)
            region_values = [pd.to_numeric(row[index], errors="coerce") for index in region_indices.values()]
            if ercot_index is not None and not is_missing(row[ercot_index]) and all(pd.notna(value) for value in region_values):
                differences.append(abs(float(sum(region_values)) - float(row[ercot_index])))
            for zone, index in region_indices.items():
                demand = pd.to_numeric(row[index], errors="coerce")
                quality = time_flag
                if pd.isna(demand):
                    quality = "source_missing" if quality == "ok" else f"{quality};source_missing"
                records.append({
                    "timestamp_utc": interval_start_utc,
                    "timestamp_local": interval_start_local,
                    "weather_zone": zone,
                    "demand_mw": demand,
                    "interval_duration_hours": 1.0,
                    "energy_mwh": demand if pd.notna(demand) else float("nan"),
                    "source_file": path.name,
                    "quality_flag": quality,
                })
        frames.append(pd.DataFrame.from_records(records))
        source_reconciliation[path.name] = {
            "timestamp_convention": "Central prevailing time with explicit DST transitions",
            "compared_intervals": len(differences),
            "maximum_regional_sum_difference_mw": max(differences) if differences else None,
            "mean_absolute_difference_mw": sum(differences) / len(differences) if differences else None,
        }
    hourly = pd.concat(frames, ignore_index=True)
    hourly["timestamp_utc"] = pd.to_datetime(hourly["timestamp_utc"], utc=True)
    hourly["timestamp_local"] = hourly["timestamp_utc"].dt.tz_convert(CENTRAL)
    hourly = hourly.sort_values(["timestamp_utc", "weather_zone"]).reset_index(drop=True)
    duplicates = int(hourly.duplicated(["timestamp_utc", "weather_zone"]).sum())
    if duplicates:
        raise ValueError(f"canonical ERCOT data has {duplicates} duplicate UTC zone records")
    report = {
        "source_files": len(paths),
        "schemas": schemas,
        "source_reconciliation": source_reconciliation,
        "duplicate_utc_zone_records": duplicates,
        "missing_demand_values": int(hourly["demand_mw"].isna().sum()),
        "quality_flags": {str(key): int(value) for key, value in hourly["quality_flag"].value_counts().items()},
    }
    return hourly, report


def expected_local_hours(start: pd.Timestamp, frequency: str) -> int:
    start_naive = start.tz_localize(None)
    end_naive = start_naive + (pd.offsets.Day(1) if frequency == "D" else pd.offsets.MonthBegin(1))
    start_utc = start_naive.tz_localize(CENTRAL).tz_convert("UTC")
    end_utc = end_naive.tz_localize(CENTRAL).tz_convert("UTC")
    return int((end_utc - start_utc).total_seconds() // 3600)


def aggregate_ercot(hourly: pd.DataFrame, frequency: str) -> pd.DataFrame:
    zones = hourly["weather_zone"].nunique()
    system = hourly.groupby(["timestamp_utc", "timestamp_local"], as_index=False).agg(
        demand_mw=("demand_mw", lambda values: values.sum(min_count=zones)),
        energy_mwh=("energy_mwh", lambda values: values.sum(min_count=zones)),
        regional_observation_count=("demand_mw", "count"),
    )
    period = system["timestamp_local"].dt.floor("D").dt.tz_localize(None)
    if frequency == "M":
        period = period.dt.to_period("M").dt.to_timestamp()
    system = system.assign(period=period)
    result = system.groupby("period", as_index=False).agg(
        total_energy_mwh=("energy_mwh", lambda values: values.sum(min_count=1)),
        average_demand_mw=("demand_mw", "mean"),
        peak_demand_mw=("demand_mw", "max"),
        minimum_demand_mw=("demand_mw", "min"),
        observation_count=("demand_mw", "count"),
    )
    result["completeness_pct"] = [
        min(100.0, count / expected_local_hours(pd.Timestamp(period).tz_localize(CENTRAL), frequency) * 100)
        for period, count in zip(result["period"], result["observation_count"])
    ]
    result = result.rename(columns={"period": "date" if frequency == "D" else "month"})
    return result


def normalize_eia(paths: list[Path]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    pattern = re.compile(r"^(?P<sector>[^_]+)_(?P<measure>Revenue|Sales|Customers|Price)_")
    for path in sorted(paths):
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheet_name = "Monthly" if "Monthly" in book.sheetnames else "Monthly-States"
        values = list(book[sheet_name].iter_rows(values_only=True))
        columns = eia_columns(values[:3])
        indices: dict[str, dict[str, int]] = {}
        for index, column in enumerate(columns):
            match = pattern.match(column)
            if match:
                indices.setdefault(match.group("sector"), {})[match.group("measure")] = index
        for row in values[3:]:
            if not isinstance(row[0], (int, float)) or not isinstance(row[1], (int, float)):
                continue
            for sector, measure_indices in indices.items():
                value = lambda name: pd.to_numeric(row[measure_indices[name]], errors="coerce") if name in measure_indices else float("nan")
                revenue_thousands = value("Revenue")
                records.append({
                    "year": int(row[0]),
                    "month": int(row[1]),
                    "state": str(row[2]).strip(),
                    "sector": sector.lower(),
                    "electricity_sales_mwh": value("Sales"),
                    "revenue_usd": revenue_thousands * 1000 if pd.notna(revenue_thousands) else float("nan"),
                    "average_price": value("Price"),
                    "customer_count": value("Customers"),
                    "source_file": path.name,
                })
    result = pd.DataFrame.from_records(records).sort_values(["year", "month", "state", "sector"])
    duplicates = result.duplicated(["year", "month", "state", "sector"])
    if duplicates.any():
        raise ValueError(f"EIA normalization produced {int(duplicates.sum())} duplicate keys")
    return result.reset_index(drop=True)


def normalize_economic(manifest: list[dict[str, Any]]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for dataset in manifest:
        dataset_id = dataset["id"]
        if dataset_id == "fred_global_supply_chain_pressure":
            path = next((RAW / dataset_id).glob("*.xls"))
            book = xlrd.open_workbook(path, on_demand=True)
            sheet = book.sheet_by_name("GSCPI Monthly Data")
            observations = [(datetime.strptime(str(sheet.cell_value(i, 0)), "%d-%b-%Y").date(), sheet.cell_value(i, 1)) for i in range(1, sheet.nrows) if sheet.cell_value(i, 0)]
        elif dataset.get("source_handler") == "fred":
            path = RAW / dataset_id / "fredgraph.csv"
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                value_column = next(column for column in reader.fieldnames or [] if column not in {"DATE", "date", "observation_date"})
                observations = [(datetime.fromisoformat(row[reader.fieldnames[0]]).date(), row[value_column]) for row in reader]
        else:
            continue
        for date_value, raw_value in observations:
            records.append({
                "dataset_id": dataset_id,
                "observation_date": date_value,
                "value": pd.to_numeric(raw_value, errors="coerce"),
                "publication_date": pd.NaT,
                "sampling_frequency": dataset.get("sampling_interval"),
                "source_url": dataset["url"],
                "source_file": path.name,
                "units": FRED_UNITS[dataset_id],
                "vintage_available": False,
            })
    return pd.DataFrame.from_records(records).sort_values(["dataset_id", "observation_date"]).reset_index(drop=True)


def normalize_gpr(path: Path, source_url: str) -> pd.DataFrame:
    book = xlrd.open_workbook(path, on_demand=True)
    sheet = book.sheet_by_index(0)
    columns = [str(value).strip() for value in sheet.row_values(0)]
    records: list[dict[str, Any]] = []
    for row_index in range(1, sheet.nrows):
        raw_date = sheet.cell_value(row_index, 0)
        if is_missing(raw_date):
            continue
        date_value = xlrd.xldate_as_datetime(raw_date, book.datemode).date()
        for column_index, indicator in enumerate(columns[1:-2], 1):
            raw_value = sheet.cell_value(row_index, column_index)
            records.append({
                "observation_date": date_value,
                "indicator": indicator,
                "value": pd.to_numeric(raw_value, errors="coerce"),
                "publication_date": pd.NaT,
                "sampling_frequency": "monthly",
                "source_url": source_url,
                "source_file": path.name,
                "units": "index/share/count as defined by source variable metadata",
                "vintage_available": False,
            })
    return pd.DataFrame.from_records(records).sort_values(["indicator", "observation_date"]).reset_index(drop=True)


def normalize_forecasts(paths: list[Path], source_records: dict[str, dict[str, Any]]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for path in sorted(paths):
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        values = list(book.worksheets[0].iter_rows(values_only=True))
        source = source_records.get(path.name, {})
        publication = source.get("publication_date")
        for row in values[2:]:
            if isinstance(row[0], (int, float)) and isinstance(row[1], (int, float)):
                records.append({
                    "forecast_year": int(row[0]),
                    "forecast_month": int(row[1]),
                    "peak_demand_mw": pd.to_numeric(row[2], errors="coerce"),
                    "energy_mwh": pd.to_numeric(row[3], errors="coerce"),
                    "forecast_variant": "ercot_adjusted",
                    "publication_date": publication,
                    "source_url": source.get("url"),
                    "source_file": path.name,
                })
    return pd.DataFrame.from_records(records).sort_values(["publication_date", "forecast_year", "forecast_month"]).reset_index(drop=True)


def parquet_summary(dataset_id: str, path: Path, frame: pd.DataFrame, date_column: str) -> dict[str, Any]:
    if dataset_id == "eia_state_monthly":
        dates = pd.to_datetime({"year": frame["year"], "month": frame["month"], "day": 1}, utc=True, errors="coerce")
    elif dataset_id == "ercot_official_forecasts":
        dates = pd.to_datetime({"year": frame["forecast_year"], "month": frame["forecast_month"], "day": 1}, utc=True, errors="coerce")
    else:
        dates = pd.to_datetime(frame[date_column], utc=True, errors="coerce")
    return {
        "dataset_id": dataset_id,
        "local_path": str(path.relative_to(ROOT)),
        "file_size": path.stat().st_size,
        "sha256": sha256(path),
        "row_count": len(frame),
        "actual_start": dates.min().isoformat() if dates.notna().any() else None,
        "actual_end": dates.max().isoformat() if dates.notna().any() else None,
        "columns": [{"name": column, "dtype": str(frame[column].dtype), "missing": int(frame[column].isna().sum())} for column in frame.columns],
        "duplicate_records": int(frame.duplicated().sum()),
        "validation_status": "valid",
    }


def main() -> int:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(ROOT / "config" / "datasets.json")
    manifest_by_id = {dataset["id"]: dataset for dataset in manifest}
    acquisition = json.loads((REPORTS / "acquisition_phase1c.json").read_text(encoding="utf-8"))
    source_records = {Path(record["path"]).name: record for record in acquisition["datasets"] if record.get("path")}

    ercot_paths = list((RAW / "ercot_hourly_load").iterdir())
    hourly, ercot_report = normalize_ercot(ercot_paths)
    daily = aggregate_ercot(hourly, "D")
    monthly = aggregate_ercot(hourly, "M")
    eia = normalize_eia(list((RAW / "eia_monthly_electricity_sales").iterdir()))
    economic = normalize_economic(manifest)
    gpr_path = next((RAW / "global_geopolitical_risk").glob("*.xls"))
    geopolitical = normalize_gpr(gpr_path, manifest_by_id["global_geopolitical_risk"]["url"])
    forecast_paths = list((RAW / "ercot_official_long_term_forecast").glob("*Monthly-Peak-Demand-and-Energy-Forecast*.xlsx"))
    forecasts = normalize_forecasts(forecast_paths, source_records)

    outputs = {
        "ercot_hourly": (hourly, "timestamp_utc"),
        "ercot_daily": (daily, "date"),
        "ercot_monthly": (monthly, "month"),
        "eia_state_monthly": (eia, "year"),
        "economic_indicators": (economic, "observation_date"),
        "geopolitical_indicators": (geopolitical, "observation_date"),
        "ercot_official_forecasts": (forecasts, "forecast_year"),
    }
    processed_inventory: list[dict[str, Any]] = []
    for dataset_id, (frame, date_column) in outputs.items():
        path = PROCESSED / f"{dataset_id}.parquet"
        frame.to_parquet(path, index=False, compression="zstd")
        processed_inventory.append(parquet_summary(dataset_id, path, frame, date_column))

    inventory = json.loads(INVENTORY.read_text(encoding="utf-8")) if INVENTORY.exists() else {}
    inventory["updated_at"] = datetime.now(timezone.utc).isoformat()
    inventory["phase1c_acquisition"] = acquisition
    inventory["processed_datasets"] = processed_inventory
    INVENTORY.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")

    regional_completeness = {
        str(zone): {
            "records": int(len(group)),
            "missing_demand": int(group["demand_mw"].isna().sum()),
            "completeness_pct": float(group["demand_mw"].notna().mean() * 100),
        }
        for zone, group in hourly.groupby("weather_zone")
    }
    reconciliation_anomalies = [
        filename for filename, details in ercot_report["source_reconciliation"].items()
        if details["maximum_regional_sum_difference_mw"] is not None
        and details["maximum_regional_sum_difference_mw"] > 1.0
    ]
    ercot_report.update({
        "actual_start_utc": hourly["timestamp_utc"].min().isoformat(),
        "actual_end_utc": hourly["timestamp_utc"].max().isoformat(),
        "hourly_region_records": len(hourly),
        "regional_completeness": regional_completeness,
        "missing_periods": daily.loc[daily["completeness_pct"] < 100, ["date", "observation_count", "completeness_pct"]].astype({"date": "str"}).to_dict("records"),
        "nonpositive_demand_records": int((hourly["demand_mw"] <= 0).sum()),
        "source_reconciliation_anomalies_over_1_mw": reconciliation_anomalies,
        "unit_conversion": "energy_mwh = demand_mw * interval_duration_hours; all observed intervals are one hour",
        "system_aggregation": "simultaneous regional demand is summed per UTC interval before peaks are calculated",
    })
    eia_dates = pd.to_datetime({"year": eia["year"], "month": eia["month"], "day": 1})
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ercot": ercot_report,
        "processed_datasets": processed_inventory,
        "eia": {
            "actual_start": eia_dates.min().date().isoformat(),
            "actual_end": eia_dates.max().date().isoformat(),
            "records": len(eia),
            "duplicate_keys": int(eia.duplicated(["year", "month", "state", "sector"]).sum()),
            "missing_values": {column: int(eia[column].isna().sum()) for column in eia.columns},
            "units": {"electricity_sales_mwh": "MWh", "revenue_usd": "USD", "average_price": "cents/kWh", "customer_count": "count"},
            "target_distinction": "EIA retail sales are not combined with ERCOT grid demand.",
        },
        "economic": {"records": len(economic), "interpolation": "none", "forward_fill": "none", "publication_dates_available": False, "vintage_available": False},
        "geopolitical": {"records": len(geopolitical), "interpolation": "none", "forward_fill": "none", "publication_dates_available": False, "vintage_available": False},
        "copernicus": "disabled; no climate artifacts downloaded",
    }
    QUALITY_JSON.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    rows = [
        "# Phase 1C data-quality report",
        "",
        f"Generated: {report['created_at']}",
        "",
        "## Processed outputs",
        "",
        "| Dataset | Rows | Coverage | Size (bytes) | Status |",
        "|---|---:|---|---:|---|",
    ]
    rows.extend(f"| {item['dataset_id']} | {item['row_count']} | {item['actual_start']} to {item['actual_end']} | {item['file_size']} | {item['validation_status']} |" for item in processed_inventory)
    rows.extend([
        "",
        "## Validation notes",
        "",
        f"- ERCOT source files: {ercot_report['source_files']}; duplicate canonical UTC/zone keys: {ercot_report['duplicate_utc_zone_records']}; missing regional demand values: {ercot_report['missing_demand_values']}.",
        f"- ERCOT coverage: {ercot_report['actual_start_utc']} through {ercot_report['actual_end_utc']}; incomplete local periods: {len(ercot_report['missing_periods'])}.",
        "- DST fall-back hours are disambiguated by source-row order; spring transitions retain only observed intervals. No interval is fabricated.",
        "- Daily and monthly ERCOT peaks are calculated from simultaneous regional sums. Energy equals demand multiplied by observed interval duration.",
        "- ERCOT regional aliases are normalized while each source schema remains recorded in the JSON report.",
        f"- Source-total reconciliation exceeds 1 MW only for: {', '.join(reconciliation_anomalies) if reconciliation_anomalies else 'none'}; detailed maximum and mean differences are in the JSON report.",
        "- EIA revenue is converted from thousand dollars to USD; average price remains cents/kWh. Retail sales remain separate from ERCOT grid demand.",
        "- Economic and geopolitical observations are not interpolated or forward-filled. Source files do not provide publication dates or historical vintages, so those fields remain unavailable.",
        "- Copernicus remains disabled and no climate data was downloaded.",
    ])
    QUALITY_MD.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"processed: {PROCESSED}")
    print(f"inventory: {INVENTORY}")
    print(f"quality: {QUALITY_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
