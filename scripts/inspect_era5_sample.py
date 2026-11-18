#!/usr/bin/env python3
"""Inspect and normalize the bounded ERA5 sample without network access."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

import pandas as pd
import xarray as xr

from weather.era5 import (
    aggregate_daily_grid,
    extract_netcdf_bundle_atomic,
    normalize_hourly,
    request_fingerprint,
    sha256_file,
    validate_era5_dataset,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=Path("data/raw/era5_historical_weather/sample_era5_2024_01.zip"))
    parser.add_argument("--extract-dir", type=Path, default=Path("data/raw/era5_historical_weather/sample_era5_2024_01"))
    parser.add_argument("--report", type=Path, default=Path("reports/phase3a_era5_sample_inspection.json"))
    parser.add_argument("--hourly-output", type=Path, default=Path("data/processed/weather/era5_sample_hourly.parquet"))
    parser.add_argument("--daily-output", type=Path, default=Path("data/processed/weather/era5_sample_daily.parquet"))
    parser.add_argument("--manifest", type=Path, default=Path("config/datasets.json"))
    return parser.parse_args()


def _parquet(dataset: xr.Dataset, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = dataset.to_dataframe().reset_index()
    if "valid_time" in frame:
        frame["timestamp_utc"] = pd.to_datetime(frame.pop("valid_time"), utc=True)
        frame["timestamp_local"] = frame["timestamp_utc"].dt.tz_convert("America/Chicago")
    frame.to_parquet(path, index=False, compression="zstd")


def main() -> int:
    args = parse_args()
    members = extract_netcdf_bundle_atomic(args.archive, args.extract_dir)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    source = next(item for item in manifest["datasets"] if item["id"] == "era5_historical_weather")
    parameters = source["request_parameters"]
    sample = parameters["sample_request"]
    request = {
        "variable": list(parameters["variables"]), "year": sample["year"], "month": sample["month"],
        "day": list(sample["day"]), "time": list(sample["time"]), "area": list(parameters["area"]),
        "data_format": parameters["data_format"], "download_format": parameters["download_format"],
        "product_type": parameters["product_type"],
    }
    instant_path = next(args.extract_dir.glob("*instant.nc"))
    accum_path = next(args.extract_dir.glob("*accum.nc"))
    inspections = {
        instant_path.name: validate_era5_dataset(instant_path, {"t2m", "d2m", "u10", "v10"}),
        accum_path.name: validate_era5_dataset(accum_path, {"tp", "ssrd"}),
    }
    with xr.open_dataset(instant_path, engine="h5netcdf") as instant, xr.open_dataset(accum_path, engine="h5netcdf") as accum:
        hourly = normalize_hourly(instant.load(), accum.load())
    daily = aggregate_daily_grid(hourly)
    _parquet(hourly, args.hourly_output)
    _parquet(daily, args.daily_output)
    report = {
        "archive": {"path": str(args.archive), "bytes": args.archive.stat().st_size, "sha256": sha256_file(args.archive)},
        "members": members,
        "request": request,
        "request_sha256": request_fingerprint(request),
        "inspection": inspections,
        "normalized": {
            "hourly_path": str(args.hourly_output), "hourly_bytes": args.hourly_output.stat().st_size,
            "daily_path": str(args.daily_output), "daily_bytes": args.daily_output.stat().st_size,
            "hourly_rows": len(hourly.to_dataframe()), "daily_rows": len(daily.to_dataframe()),
        },
        "semantics": {
            "accumulations": "one-hour totals ending at valid_time; not differenced",
            "precipitation": "m multiplied by 1000 to mm",
            "solar": "J/m2 retained; divided by 1e6 for MJ/m2 and by 3600 for hourly mean W/m2",
            "local_time": "America/Chicago; accumulated intervals assigned by interval end minus 1 ns",
            "degree_days": "computed per grid cell from local-day mean temperature before spatial aggregation",
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    sidecar = args.archive.with_name(f"{args.archive.name}.request.json")
    sidecar_temporary = sidecar.with_name(f".{sidecar.name}.part")
    sidecar_temporary.write_text(json.dumps({
        "dataset_id": source["id"], "dataset": parameters["dataset"], "request": request,
        "request_sha256": report["request_sha256"], "path": str(args.archive),
        "sha256": report["archive"]["sha256"], "members": members,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sidecar_temporary.replace(sidecar)
    print(json.dumps(report["normalized"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
