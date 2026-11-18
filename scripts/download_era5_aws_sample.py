#!/usr/bin/env python3
"""Download one bounded ERA5 month from the public AWS Icechunk mirror."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import time
from datetime import UTC, datetime
from pathlib import Path

import icechunk
import numpy as np
import xarray as xr


BUCKET = "earthmover-icechunk-era5"
PREFIX = "icechunkV2"
GROUP = "single/temporal"
VARIABLES = ("t2m", "d2m", "u10", "v10", "tp", "ssrd")
NORTH, WEST, SOUTH, EAST = 36.5, -106.75, 25.75, -93.5


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", default="2004-01", help="single month in YYYY-MM form")
    parser.add_argument(
        "--timestamps",
        help="comma-separated UTC timestamps; when set, overrides --month",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/raw/era5_historical_weather/aws_sample_era5_2004_01.nc"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("reports/phase3a_era5_aws_sample.json"),
    )
    return parser.parse_args()


def network_received_bytes() -> int:
    total = 0
    for line in Path("/proc/net/dev").read_text(encoding="utf-8").splitlines()[2:]:
        fields = line.split(":", 1)[1].split()
        total += int(fields[0])
    return total


def main() -> int:
    args = parse_args()
    total_started = time.perf_counter()
    network_started = network_received_bytes()
    if args.timestamps:
        requested_times = np.asarray(
            [np.datetime64(value.strip()) for value in args.timestamps.split(",")],
            dtype="datetime64[ns]",
        )
        if requested_times.size == 0 or np.unique(requested_times).size != requested_times.size:
            raise ValueError("timestamps must be non-empty and unique")
        time_selector: slice | np.ndarray = requested_times
        expected_hours = int(requested_times.size)
        request_period: dict[str, object] = {
            "timestamps": [str(value) for value in requested_times]
        }
    else:
        start = np.datetime64(f"{args.month}-01T00:00:00")
        next_month = (
            start.astype("datetime64[M]") + np.timedelta64(1, "M")
        ).astype("datetime64[ns]")
        end = next_month - np.timedelta64(1, "h")
        time_selector = slice(start, end)
        expected_hours = int((next_month - start) / np.timedelta64(1, "h"))
        request_period = {"month": args.month}

    storage = icechunk.s3_storage(
        bucket=BUCKET, prefix=PREFIX, region="us-east-1", anonymous=True
    )
    repository = icechunk.Repository.open(storage)
    session = repository.readonly_session("main")
    source = xr.open_zarr(
        session.store, group=GROUP, consolidated=False, chunks=None
    )
    missing = sorted(set(VARIABLES) - set(source.data_vars))
    if missing:
        raise ValueError(f"AWS ERA5 mirror is missing variables: {missing}")

    # AWS uses 0..360 longitude; the validated Texas box uses -180..180.
    west_360, east_360 = WEST % 360, EAST % 360
    subset = source[list(VARIABLES)].sel(
        valid_time=time_selector,
        latitude=slice(NORTH, SOUTH),
        longitude=slice(west_360, east_360),
    )
    expected = {"valid_time": expected_hours, "latitude": 44, "longitude": 54}
    actual = {name: int(subset.sizes[name]) for name in expected}
    if actual != expected:
        raise ValueError(f"unexpected subset dimensions: expected {expected}, got {actual}")
    times = subset.valid_time.values
    if len(np.unique(times)) != expected_hours:
        raise ValueError("duplicate or missing hourly timestamps")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.part")
    temporary.unlink(missing_ok=True)
    subset.attrs.update(
        {
            "energycast_source": "AWS Open Data Earthmover Icechunk ERA5",
            "energycast_bucket": BUCKET,
            "energycast_prefix": PREFIX,
            "energycast_group": GROUP,
            "energycast_requested_bounds": f"{NORTH},{WEST},{SOUTH},{EAST}",
        }
    )
    download_started = time.perf_counter()
    subset.load()
    download_seconds = time.perf_counter() - download_started
    network_bytes = max(0, network_received_bytes() - network_started)
    write_started = time.perf_counter()
    subset.to_netcdf(temporary, engine="h5netcdf")
    write_seconds = time.perf_counter() - write_started
    with temporary.open("rb") as stream:
        if stream.read(8) != b"\x89HDF\r\n\x1a\n":
            raise ValueError("downloaded sample is not NetCDF4/HDF5")
    temporary.replace(args.output)

    with xr.open_dataset(args.output, engine="h5netcdf") as saved:
        null_counts = {
            name: int(saved[name].isnull().sum().load().item()) for name in VARIABLES
        }
        report = {
            "created_at": datetime.now(UTC).isoformat(),
            "status": "validated" if not any(null_counts.values()) else "incomplete",
            "source": {
                "registry": "https://registry.opendata.aws/earthmover-era5/",
                "bucket": BUCKET,
                "prefix": PREFIX,
                "group": GROUP,
                "repository_snapshot": str(session.snapshot_id),
            },
            "request": {
                **request_period,
                "bounds": [NORTH, WEST, SOUTH, EAST],
                "variables": list(VARIABLES),
            },
            "artifact": {
                "path": str(args.output),
                "bytes": args.output.stat().st_size,
                "sha256": sha256_file(args.output),
            },
            "dimensions": {name: int(size) for name, size in saved.sizes.items()},
            "coverage": {
                "start": str(saved.valid_time.values[0]),
                "end": str(saved.valid_time.values[-1]),
            },
            "coordinates": {
                "latitude": [float(saved.latitude.min()), float(saved.latitude.max())],
                "longitude_0_360": [float(saved.longitude.min()), float(saved.longitude.max())],
            },
            "units": {name: saved[name].attrs.get("units") for name in VARIABLES},
            "dtypes": {name: str(saved[name].dtype) for name in VARIABLES},
            "missing_values": null_counts,
            "performance": {
                "remote_read_seconds": download_seconds,
                "local_netcdf_write_seconds": write_seconds,
                "total_seconds": time.perf_counter() - total_started,
                "host_network_received_bytes": network_bytes,
                "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
                "note": "network bytes are a host-interface delta and may include unrelated host traffic",
            },
            "source_chunking": {
                name: list(source[name].encoding.get("chunks", ())) for name in VARIABLES
            },
        }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report_tmp = args.report.with_name(f".{args.report.name}.part")
    report_tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_tmp.replace(args.report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
