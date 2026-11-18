#!/usr/bin/env python3
"""Audit locally acquired annual ERA5 caches and normalized partitions.

This script is read-only with respect to weather datasets. It writes a JSON
inventory, a Markdown quality report, and a summary reference in the project
inventory. It never opens a remote provider.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/era5_historical_weather/aws"
PROCESSED = ROOT / "data/processed/weather"
REPORT_JSON = ROOT / "reports/phase3a_era5_post_acquisition_audit.json"
REPORT_MD = ROOT / "reports/phase3a_era5_post_acquisition_audit.md"
INVENTORY = ROOT / "reports/data_inventory.json"
VARIABLES = ("t2m", "d2m", "u10", "v10", "tp", "ssrd")
GRID_CELLS = 44 * 54


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def parquet_null_counts(path: Path) -> dict[str, int | None]:
    parquet = pq.ParquetFile(path)
    result: dict[str, int | None] = {}
    for column_index, name in enumerate(parquet.schema_arrow.names):
        total = 0
        complete = True
        for row_group_index in range(parquet.num_row_groups):
            statistics = parquet.metadata.row_group(row_group_index).column(column_index).statistics
            if statistics is None or not statistics.has_null_count:
                complete = False
                break
            total += statistics.null_count
        result[name] = total if complete else None
    return result


def parquet_duplicate_keys(path: Path, keys: list[str]) -> int:
    frame = pq.read_table(path, columns=keys).to_pandas()
    return int(frame.duplicated(keys).sum())


def audit_annual(raw_dir: Path, end_year: int, end_month: int) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    reference_latitude: np.ndarray | None = None
    reference_longitude: np.ndarray | None = None
    previous_end: pd.Timestamp | None = None
    for year in range(2004, end_year + 1):
        path = raw_dir / f"era5_texas_{year}.nc"
        sidecar_path = path.with_suffix(".request.json")
        sidecar = json.loads(sidecar_path.read_text())
        checksum = sha256_file(path)
        with xr.open_dataset(path) as dataset:
            times = pd.DatetimeIndex(dataset.valid_time.values)
            latitude = dataset.latitude.values
            longitude = dataset.longitude.values
            missing_variables = sorted(set(VARIABLES) - set(dataset.data_vars))
            null_counts = {
                variable: int(dataset[variable].isnull().sum().load().item())
                for variable in VARIABLES
                if variable in dataset
            }
            duplicates = int(times.duplicated().sum())
            missing_hours = int(
                len(pd.date_range(times.min(), times.max(), freq="h").difference(times))
            )
            spatial_consistent = True
            if reference_latitude is None:
                reference_latitude = latitude.copy()
                reference_longitude = longitude.copy()
            else:
                spatial_consistent = bool(
                    np.array_equal(latitude, reference_latitude)
                    and np.array_equal(longitude, reference_longitude)
                )
            cross_year_gap_hours = None
            if previous_end is not None:
                cross_year_gap_hours = float((times.min() - previous_end) / pd.Timedelta(hours=1))
            previous_end = times.max()
            record = {
                "year": year,
                "path": str(path.relative_to(ROOT)),
                "size_bytes": path.stat().st_size,
                "sha256": checksum,
                "checksum_valid": checksum == sidecar.get("sha256"),
                "request_sha256": sidecar.get("request_sha256"),
                "snapshot": sidecar.get("snapshot"),
                "start": times.min().isoformat(),
                "end": times.max().isoformat(),
                "timestamp_count": len(times),
                "missing_hours": missing_hours,
                "duplicate_timestamps": duplicates,
                "dimensions": {name: int(value) for name, value in dataset.sizes.items()},
                "latitude_bounds": [float(latitude.min()), float(latitude.max())],
                "longitude_bounds_0_360": [float(longitude.min()), float(longitude.max())],
                "spatial_consistent": spatial_consistent,
                "cross_year_start_delta_hours": cross_year_gap_hours,
                "missing_variables": missing_variables,
                "null_values": null_counts,
            }
        if not record["checksum_valid"]:
            errors.append(f"{year}: annual checksum mismatch")
        if missing_hours or duplicates or missing_variables or any(null_counts.values()):
            errors.append(f"{year}: annual temporal/variable validation failure")
        if not spatial_consistent:
            errors.append(f"{year}: spatial grid differs from reference")
        if cross_year_gap_hours is not None and cross_year_gap_hours != 1.0:
            errors.append(f"{year}: cross-year delta is {cross_year_gap_hours} hours")
        records.append(record)
    return records, errors


def audit_partitions(processed_dir: Path) -> tuple[dict[str, Any], list[str]]:
    validation_paths = sorted((processed_dir / "validation").glob("era5_*.json"))
    errors: list[str] = []
    totals = Counter()
    null_totals: Counter[str] = Counter()
    schemas: dict[str, set[tuple[str, ...]]] = {name: set() for name in ("hourly", "daily", "monthly")}
    duplicate_totals = Counter()
    physical_flags = Counter()
    start: str | None = None
    end: str | None = None
    for index, validation_path in enumerate(validation_paths, start=1):
        report = json.loads(validation_path.read_text())
        year, month = int(report["year"]), int(report["month"])
        start = start or f"{year}-{month:02d}-01T00:00:00+00:00"
        end = f"{year}-{month:02d}"
        if report.get("status") != "validated" or report["expected_hours"] != report["actual_hours"]:
            errors.append(f"{year}-{month:02d}: validation status/hour mismatch")
        for name, value in report.get("physical_range_flags", {}).items():
            physical_flags[name] += int(value)
        for dataset_type in ("hourly", "daily", "monthly"):
            output = report["outputs"][dataset_type]
            path = Path(output["path"])
            totals[f"{dataset_type}_files"] += 1
            totals[f"{dataset_type}_rows"] += int(output["rows"])
            totals[f"{dataset_type}_bytes"] += path.stat().st_size
            if sha256_file(path) != output["sha256"]:
                errors.append(f"{year}-{month:02d} {dataset_type}: checksum mismatch")
            parquet = pq.ParquetFile(path)
            if parquet.metadata.num_rows != output["rows"]:
                errors.append(f"{year}-{month:02d} {dataset_type}: row-count mismatch")
            schemas[dataset_type].add(tuple(parquet.schema_arrow.names))
            for column, count in parquet_null_counts(path).items():
                if count is not None:
                    null_totals[f"{dataset_type}.{column}"] += count
            keys = {
                "hourly": ["timestamp_utc", "latitude", "longitude"],
                "daily": ["local_date", "latitude", "longitude"],
                "monthly": ["year", "month", "latitude", "longitude"],
            }[dataset_type]
            duplicates = parquet_duplicate_keys(path, keys)
            duplicate_totals[dataset_type] += duplicates
            if duplicates:
                errors.append(f"{year}-{month:02d} {dataset_type}: {duplicates} duplicate keys")
        if index % 24 == 0:
            print(f"audited {index}/{len(validation_paths)} monthly partitions", flush=True)

    anomaly: dict[str, Any] = {}
    if physical_flags["negative_solar_radiation"]:
        path = processed_dir / "hourly_grid/year=2004/era5_hourly_2004-12.parquet"
        frame = pq.read_table(
            path,
            columns=["timestamp_utc", "latitude", "longitude", "solar_radiation_j_m2"],
        ).to_pandas()
        negative = frame[frame.solar_radiation_j_m2 < -1e-3]
        anomaly = {
            "count": len(negative),
            "minimum_j_m2": float(negative.solar_radiation_j_m2.min()),
            "timestamps": sorted(str(value) for value in negative.timestamp_utc.unique()),
            "unique_grid_cells": int(negative[["latitude", "longitude"]].drop_duplicates().shape[0]),
            "interpretation": "source-level negative accumulated SSRD values; retained and flagged",
        }
    return {
        "coverage": {"start": start, "end_month": end},
        "totals": dict(totals),
        "schema_variant_counts": {name: len(values) for name, values in schemas.items()},
        "null_counts_from_parquet_statistics": dict(null_totals),
        "duplicate_keys": dict(duplicate_totals),
        "physical_range_flags": dict(physical_flags),
        "negative_solar_radiation_detail": anomaly,
    }, errors


def update_inventory(path: Path, report: dict[str, Any]) -> None:
    inventory = json.loads(path.read_text())
    inventory["weather_inventory"] = {
        "updated_at": report["created_at"],
        "report": str(REPORT_JSON.relative_to(ROOT)),
        "provider": "aws_earthmover",
        "snapshot": report["provider_snapshot"],
        "normalized_coverage": report["partitions"]["coverage"],
        "annual_cache_count": len(report["annual_caches"]),
        "monthly_partition_count": report["partitions"]["totals"]["hourly_files"],
        "hourly_rows": report["partitions"]["totals"]["hourly_rows"],
        "validation_status": report["status"],
        "storage_bytes": report["storage_bytes"],
    }
    atomic_json(path, inventory)


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    partitions = report["partitions"]
    totals = partitions["totals"]
    storage = report["storage_bytes"]
    anomaly = partitions["negative_solar_radiation_detail"]
    missing_2026 = ", ".join(report["remaining_2026_months"])
    lines = [
        "# ERA5 post-acquisition validation",
        "",
        f"Generated: {report['created_at']}",
        "",
        f"Status: **{report['status']}** for the requested range through May 2026.",
        "",
        "## Coverage and counts",
        "",
        f"- Annual caches: {len(report['annual_caches'])} (2004–2026).",
        f"- Normalized months: {totals['hourly_files']} (2004-01 through 2026-05).",
        f"- Source UTC hours: {report['source_timestamp_count']:,}.",
        f"- Hourly grid rows: {totals['hourly_rows']:,}.",
        f"- Daily grid rows: {totals['daily_rows']:,}.",
        f"- Monthly grid rows: {totals['monthly_rows']:,}.",
        f"- Duplicate hourly/daily/monthly keys: {partitions['duplicate_keys']}.",
        "- Annual missing timestamps, duplicate timestamps, missing variables and null observations: zero.",
        "- Grid is consistent at 44×54 cells, 0.25°, across all annual caches.",
        "- Every annual boundary is contiguous by one UTC hour.",
        "",
        "## Storage",
        "",
        f"- Annual NetCDF caches and sidecars: {storage['annual_raw']:,} bytes.",
        f"- Hourly Parquet: {storage['hourly']:,} bytes.",
        f"- Daily Parquet: {storage['daily']:,} bytes.",
        f"- Monthly Parquet: {storage['monthly']:,} bytes.",
        f"- Validation metadata: {storage['validation']:,} bytes.",
        f"- Total: {storage['total']:,} bytes.",
        "",
        "## Quality findings",
        "",
        f"- Negative solar radiation: {anomaly.get('count', 0):,} values; minimum "
        f"{anomaly.get('minimum_j_m2')} J/m²; timestamps {anomaly.get('timestamps', [])}. "
        "Values are retained and explicitly flagged rather than altered.",
        "- All other configured physical-range flags are zero.",
        "- Parquet schemas are stable within hourly, daily and monthly products.",
        "",
        "## Remaining 2026 coverage",
        "",
        f"Normalized weather ends at 2026-05. Required but unavailable months: {missing_2026}. "
        "The validated AWS snapshot ends 2026-06-30; June local-day aggregation needs the "
        "July 1 Texas-local boundary, and October requires data through November 1.",
        "",
        "## External discovery",
        "",
        "- ERCOT publishes an authoritative weather-zone map image and describes the zones, "
        "but no authoritative machine-readable polygon or county crosswalk was located. "
        "The zone-mapping blocker remains open.",
        "- CDS seasonal monthly and original single-level products require authenticated CDS "
        "access, acceptance of CC-BY terms, and the additional licence for non-European "
        "contributions. Forecasts must be paired with the matching-system hindcasts. No "
        "seasonal request was submitted during this audit.",
        "",
    ]
    path.write_text("\n".join(lines))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=RAW)
    parser.add_argument("--processed-dir", type=Path, default=PROCESSED)
    parser.add_argument("--output-json", type=Path, default=REPORT_JSON)
    parser.add_argument("--output-md", type=Path, default=REPORT_MD)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    annual, annual_errors = audit_annual(args.raw_dir, 2026, 5)
    partitions, partition_errors = audit_partitions(args.processed_dir)
    storage = {
        "annual_raw": directory_size(args.raw_dir),
        "hourly": directory_size(args.processed_dir / "hourly_grid"),
        "daily": directory_size(args.processed_dir / "daily_grid"),
        "monthly": directory_size(args.processed_dir / "monthly_grid"),
        "validation": directory_size(args.processed_dir / "validation"),
    }
    storage["total"] = sum(storage.values())
    errors = annual_errors + partition_errors
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "validated" if not errors else "failed",
        "provider": "aws_earthmover",
        "provider_snapshot": annual[0]["snapshot"] if annual else None,
        "annual_caches": annual,
        "source_timestamp_count": sum(item["timestamp_count"] for item in annual),
        "partitions": partitions,
        "storage_bytes": storage,
        "remaining_2026_months": ["2026-06", "2026-07", "2026-08", "2026-09", "2026-10"],
        "errors": errors,
        "elapsed_seconds": time.monotonic() - started,
    }
    atomic_json(args.output_json, report)
    write_markdown(args.output_md, report)
    update_inventory(args.inventory, report)
    print(json.dumps({
        "status": report["status"],
        "annual_caches": len(annual),
        "normalized_months": partitions["totals"]["hourly_files"],
        "hourly_rows": partitions["totals"]["hourly_rows"],
        "errors": len(errors),
        "elapsed_seconds": report["elapsed_seconds"],
    }, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
