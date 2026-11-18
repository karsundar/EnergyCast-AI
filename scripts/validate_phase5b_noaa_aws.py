#!/usr/bin/env python3
"""Bounded NOAA/AWS CFSv2 range-download and CDS comparison."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.request import Request, urlopen

import cfgrib
import numpy as np
import pandas as pd
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
KEY = ("cfs.20240101/06/monthly_grib_01/"
       "flxf.01.2024010106.202402.avrg.grib.grb2")
BASE = "https://noaa-cfs-pds.s3.amazonaws.com/"
TARGETS = {
    ("DSWRF", "surface"), ("PRATE", "surface"),
    ("UGRD", "10 m above ground"), ("VGRD", "10 m above ground"),
    ("TMP", "2 m above ground"), ("SPFH", "2 m above ground"),
    ("PRES", "surface"),
}


def fetch(url: str, byte_range: tuple[int, int] | None = None) -> bytes:
    headers = {"User-Agent": "EnergyCast-AI/phase5b-bounded-validation"}
    if byte_range:
        headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
    with urlopen(Request(url, headers=headers), timeout=120) as response:
        return response.read()


def ranges_from_index(payload: str, object_size: int) -> list[tuple[int, int]]:
    records: list[tuple[int, str, str]] = []
    offsets: list[int] = []
    for line in payload.splitlines():
        match = re.match(r"[^:]+:(\d+):[^:]+:([^:]+):([^:]+):", line)
        if not match:
            continue
        offset, variable, level = int(match.group(1)), match.group(2), match.group(3)
        records.append((offset, variable, level))
        offsets.append(offset)
    unique = sorted(set(offsets))
    ends = {offset: (unique[index + 1] - 1 if index + 1 < len(unique) else object_size - 1)
            for index, offset in enumerate(unique)}
    selected = sorted({offset for offset, variable, level in records if (variable, level) in TARGETS})
    if len(selected) != 6:  # U/V share one multi-field GRIB message.
        raise ValueError(f"expected six selected GRIB messages, found {len(selected)}")
    return [(offset, ends[offset]) for offset in selected]


def datasets(path: Path) -> dict[str, xr.DataArray]:
    arrays: dict[str, xr.DataArray] = {}
    for dataset in cfgrib.open_datasets(path, backend_kwargs={"indexpath": ""}):
        for name, value in dataset.data_vars.items():
            arrays[name] = value
    required = {"avg_2t", "avg_2sh", "avg_sp", "avg_10u", "avg_10v", "prate", "sdswrf"}
    missing = required - arrays.keys()
    if missing:
        raise ValueError(f"missing decoded variables: {sorted(missing)}")
    return arrays


def texas(array: xr.DataArray) -> xr.DataArray:
    longitude = array.longitude
    west, east = (-106.75) % 360, (-93.5) % 360
    return array.where((array.latitude <= 36.5) & (array.latitude >= 25.75)
                       & (longitude >= west) & (longitude <= east), drop=True)


def main() -> int:
    started = time.monotonic()
    index = fetch(BASE + KEY + ".idx").decode()
    head = Request(BASE + KEY, method="HEAD", headers={"User-Agent": "EnergyCast-AI/phase5b-bounded-validation"})
    with urlopen(head, timeout=60) as response:
        size = int(response.headers["Content-Length"])
        etag = response.headers["ETag"].strip('"')
    selected = ranges_from_index(index, size)
    destination = ROOT / "data" / "raw" / "weather" / "seasonal" / "aws_cfsv2_bounded_2024010106_202402.grb2"
    destination.parent.mkdir(parents=True, exist_ok=True)
    transferred = 0
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        temporary = Path(handle.name)
        for start, end in selected:
            block = fetch(BASE + KEY, (start, end))
            if len(block) != end - start + 1 or block[:4] != b"GRIB" or block[-4:] != b"7777":
                raise ValueError(f"invalid ranged GRIB message {start}-{end}")
            handle.write(block)
            transferred += len(block)
    try:
        decoded = datasets(temporary)
        subset = {name: texas(value) for name, value in decoded.items()}
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    native = xr.Dataset({name: value.reset_coords(drop=True) for name, value in subset.items()})
    native_frame = native.to_dataframe().reset_index()
    native_frame["longitude"] = ((native_frame.longitude + 180) % 360) - 180
    normalized = ROOT / "data" / "processed" / "weather" / "seasonal" / "aws_cfsv2_bounded_2024010106_202402.csv.gz"
    native_frame.to_csv(normalized, index=False, compression="gzip")
    report = {
        "status": "bounded_operational_sample_validated",
        "provider": "noaa_aws_noaa-cfs-pds", "key": KEY,
        "source_object_bytes": size, "source_etag_md5": etag,
        "selected_message_ranges": selected, "transferred_bytes": transferred,
        "transfer_reduction_pct": round(100 * (1 - transferred / size), 3),
        "selected_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "decoded_variables": sorted(decoded),
        "native_grid": {"latitude": int(subset["avg_2t"].sizes["latitude"]),
                        "longitude": int(subset["avg_2t"].sizes["longitude"])},
        "native_subset_rows": len(native_frame),
        "native_subset_path": str(normalized),
        "seconds": time.monotonic() - started,
        "limitations": [
            "AWS operational objects begin later than the required hindcast period",
            "native Gaussian grid requires documented regridding to the CDS one-degree grid",
            "dewpoint must be derived from specific humidity, surface pressure and temperature",
        ],
    }
    output = ROOT / "reports" / "phase5b_noaa_aws_bounded_validation.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
