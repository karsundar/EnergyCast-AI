#!/usr/bin/env python3
"""Retrieve the bounded four-timestamp ERA5 validation sample from a cloud provider."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import xarray as xr

from acquisition.era5_providers import (
    CANONICAL_VARIABLES,
    CopernicusArcoProvider,
    Era5SubsetRequest,
    GoogleArcoProvider,
    canonical_subset,
)


TIMESTAMPS = tuple(
    np.datetime64(value)
    for value in (
        "2024-01-01T00:00:00",
        "2024-01-01T06:00:00",
        "2024-01-01T12:00:00",
        "2024-01-01T18:00:00",
    )
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("google", "copernicus"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def network_received_bytes() -> int:
    return sum(
        int(line.split(":", 1)[1].split()[0])
        for line in Path("/proc/net/dev").read_text(encoding="utf-8").splitlines()[2:]
    )


def main() -> int:
    args = parse_args()
    provider = GoogleArcoProvider() if args.provider == "google" else CopernicusArcoProvider()
    started = time.perf_counter()
    network_start = network_received_bytes()
    source = provider.open()
    source_chunking = {
        name: list(source[name].encoding.get("chunks", ()))
        for name in source.data_vars
        if name in CANONICAL_VARIABLES
        or name
        in {
            "2m_temperature",
            "2m_dewpoint_temperature",
            "10m_u_component_of_wind",
            "10m_v_component_of_wind",
            "total_precipitation",
            "surface_solar_radiation_downwards",
        }
    }
    request = Era5SubsetRequest(TIMESTAMPS, 36.5, -106.75, 25.75, -93.5)
    subset = canonical_subset(source, request)
    expected = {"valid_time": 4, "latitude": 44, "longitude": 54}
    actual = {name: int(subset.sizes[name]) for name in expected}
    if actual != expected:
        raise ValueError(f"unexpected dimensions: {actual}; expected {expected}")
    remote_started = time.perf_counter()
    subset.load()
    remote_seconds = time.perf_counter() - remote_started
    network_bytes = max(0, network_received_bytes() - network_start)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.part")
    temporary.unlink(missing_ok=True)
    subset.attrs.update({f"energycast_{key}": str(value) for key, value in provider.provenance().items()})
    write_started = time.perf_counter()
    subset.to_netcdf(temporary, engine="h5netcdf")
    write_seconds = time.perf_counter() - write_started
    temporary.replace(args.output)
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "status": "validated",
        "provider": provider.provenance(),
        "artifact": {
            "path": str(args.output),
            "bytes": args.output.stat().st_size,
            "sha256": sha256_file(args.output),
        },
        "request": {
            "timestamps": [str(value) for value in TIMESTAMPS],
            "bounds": [36.5, -106.75, 25.75, -93.5],
            "variables": list(CANONICAL_VARIABLES),
        },
        "dimensions": actual,
        "dtypes": {name: str(subset[name].dtype) for name in CANONICAL_VARIABLES},
        "units": {name: subset[name].attrs.get("units") for name in CANONICAL_VARIABLES},
        "missing_values": {
            name: int(subset[name].isnull().sum().item()) for name in CANONICAL_VARIABLES
        },
        "source_chunking": source_chunking,
        "performance": {
            "remote_read_seconds": remote_seconds,
            "local_netcdf_write_seconds": write_seconds,
            "total_seconds": time.perf_counter() - started,
            "host_network_received_bytes": network_bytes,
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            "note": "network bytes are a host-interface delta and may include unrelated host traffic",
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
