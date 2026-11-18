#!/usr/bin/env python3
"""Validate and normalize one cached ERA5 month without loading historical data."""

from __future__ import annotations

import argparse
import calendar
import json
import os
import resource
import time
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from weather.era5 import aggregate_daily_grid, normalize_hourly, sha256_file, validate_era5_dataset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True, choices=range(1, 13))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/era5_historical_weather"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/weather"))
    parser.add_argument("--report-dir", type=Path, default=Path("reports/era5_months"))
    return parser.parse_args()


def _paths(raw_dir: Path, year: int, month: int) -> tuple[Path, Path, Path]:
    stem = f"era5_{year}_{month:02d}"
    archive = raw_dir / f"{stem}.zip"
    extracted = raw_dir / stem
    return archive, extracted / "data_stream-oper_stepType-instant.nc", extracted / "data_stream-oper_stepType-accum.nc"


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _atomic_parquet(frame: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    frame.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, destination)


def _open_pair(instant_path: Path, accum_path: Path) -> tuple[xr.Dataset, xr.Dataset]:
    instant = xr.open_dataset(instant_path, engine="h5netcdf")
    accum = xr.open_dataset(accum_path, engine="h5netcdf")
    return instant, accum


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    archive, instant_path, accum_path = _paths(args.raw_dir, args.year, args.month)
    if not archive.is_file() or not instant_path.is_file() or not accum_path.is_file():
        raise SystemExit(f"missing cached source files for {args.year}-{args.month:02d}")
    instant_inspection = validate_era5_dataset(instant_path, {"t2m", "d2m", "u10", "v10"})
    accum_inspection = validate_era5_dataset(accum_path, {"tp", "ssrd"})
    expected_hours = calendar.monthrange(args.year, args.month)[1] * 24
    expected_times = pd.date_range(f"{args.year}-{args.month:02d}-01", periods=expected_hours, freq="h")
    actual_times = pd.DatetimeIndex(xr.open_dataset(instant_path, engine="h5netcdf").valid_time.values)
    missing_times = expected_times.difference(actual_times)
    extra_times = actual_times.difference(expected_times)
    source_valid = (
        len(actual_times) == expected_hours and not missing_times.size and not extra_times.size
        and instant_inspection["dimensions"] == {"valid_time": expected_hours, "latitude": 44, "longitude": 54}
        and accum_inspection["dimensions"] == {"valid_time": expected_hours, "latitude": 44, "longitude": 54}
    )
    if not source_valid:
        raise SystemExit(f"source validation failed for {args.year}-{args.month:02d}")

    instant, accum = _open_pair(instant_path, accum_path)
    try:
        hourly = normalize_hourly(instant, accum).load()
    finally:
        instant.close()
        accum.close()
    physical_flags = {
        "temperature_c_outside_minus90_60": int(((hourly.temperature_c < -90) | (hourly.temperature_c > 60)).sum()),
        "relative_humidity_outside_0_100": int(((hourly.relative_humidity_pct < 0) | (hourly.relative_humidity_pct > 100)).sum()),
        "wind_speed_above_100_mps": int((hourly.wind_speed_mps > 100).sum()),
        "negative_precipitation": int((hourly.precipitation_mm < -1e-6).sum()),
        "negative_solar_radiation": int((hourly.solar_radiation_j_m2 < -1e-3).sum()),
    }
    hourly_frame = hourly.to_dataframe().reset_index()
    hourly_frame["timestamp_utc"] = pd.to_datetime(hourly_frame.pop("valid_time"), utc=True)
    hourly_frame["timestamp_local"] = hourly_frame["timestamp_utc"].dt.tz_convert("America/Chicago")
    hourly_path = args.output_dir / "hourly" / f"year={args.year}" / f"month={args.month:02d}" / "part.parquet"
    _atomic_parquet(hourly_frame, hourly_path)
    del hourly_frame

    sources = [(instant_path, accum_path)]
    next_year, next_value = _next_month(args.year, args.month)
    _, next_instant, next_accum = _paths(args.raw_dir, next_year, next_value)
    boundary_available = next_instant.is_file() and next_accum.is_file()
    if boundary_available:
        sources.append((next_instant, next_accum))
    instant_sets, accum_sets = [], []
    try:
        for source_instant, source_accum in sources:
            source_i, source_a = _open_pair(source_instant, source_accum)
            instant_sets.append(source_i)
            accum_sets.append(source_a)
        combined_instant = xr.concat(instant_sets, dim="valid_time").sortby("valid_time")
        combined_accum = xr.concat(accum_sets, dim="valid_time").sortby("valid_time")
        combined = normalize_hourly(combined_instant, combined_accum)
        daily = aggregate_daily_grid(combined)
        target_start = np.datetime64(f"{args.year}-{args.month:02d}-01")
        last_day = calendar.monthrange(args.year, args.month)[1]
        target_end = np.datetime64(f"{args.year}-{args.month:02d}-{last_day:02d}")
        daily = daily.sel(local_date=slice(target_start, target_end)).load()
    finally:
        for dataset in instant_sets + accum_sets:
            dataset.close()
    daily_frame = daily.to_dataframe().reset_index()
    daily_path = args.output_dir / "daily" / f"year={args.year}" / f"month={args.month:02d}" / "part.parquet"
    _atomic_parquet(daily_frame, daily_path)
    monthly = daily_frame.groupby(["latitude", "longitude"], as_index=False).agg(
        temperature_mean_c=("temperature_mean_c", "mean"), temperature_min_c=("temperature_min_c", "min"),
        temperature_max_c=("temperature_max_c", "max"), relative_humidity_mean_pct=("relative_humidity_mean_pct", "mean"),
        wind_speed_mean_mps=("wind_speed_mean_mps", "mean"), precipitation_total_mm=("precipitation_total_mm", "sum"),
        solar_energy_total_mj_m2=("solar_energy_total_mj_m2", "sum"), hdd_65f=("hdd_65f", "sum"),
        cdd_65f=("cdd_65f", "sum"), observation_count=("observation_count", "sum"),
        expected_observation_count=("expected_observation_count", "sum"),
    )
    monthly.insert(0, "year", args.year)
    monthly.insert(1, "month", args.month)
    monthly["completeness_pct"] = monthly.observation_count / monthly.expected_observation_count * 100.0
    monthly["boundary_complete"] = boundary_available & (monthly.completeness_pct == 100.0)
    monthly_path = args.output_dir / "monthly" / f"year={args.year}" / f"month={args.month:02d}" / "part.parquet"
    _atomic_parquet(monthly, monthly_path)
    report = {
        "month": f"{args.year}-{args.month:02d}", "status": "validated" if source_valid else "failed",
        "archive": {"path": str(archive), "bytes": archive.stat().st_size, "sha256": sha256_file(archive)},
        "expected_timestamps": expected_hours, "actual_timestamps": len(actual_times),
        "missing_timestamps": [value.isoformat() for value in missing_times],
        "extra_timestamps": [value.isoformat() for value in extra_times],
        "grid": {"latitude": 44, "longitude": 54, "cells": 2376, "resolution_degrees": 0.25},
        "source_missing_values": {**instant_inspection["missing_values"], **accum_inspection["missing_values"]},
        "physical_flags": physical_flags, "boundary_available": boundary_available,
        "daily_min_completeness_pct": float(daily_frame.completeness_pct.min()),
        "monthly_min_completeness_pct": float(monthly.completeness_pct.min()),
        "outputs": {
            "hourly": {"path": str(hourly_path), "bytes": hourly_path.stat().st_size, "rows": expected_hours * 2376},
            "daily": {"path": str(daily_path), "bytes": daily_path.stat().st_size, "rows": len(daily_frame)},
            "monthly": {"path": str(monthly_path), "bytes": monthly_path.stat().st_size, "rows": len(monthly)},
        },
        "duration_seconds": time.monotonic() - started,
        "peak_ram_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0,
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.report_dir / f"{args.year}_{args.month:02d}.json"
    temporary = report_path.with_name(f".{report_path.name}.part")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, report_path)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
