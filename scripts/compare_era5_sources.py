#!/usr/bin/env python3
"""Compare bounded AWS ERA5 artifacts with matching CDS NetCDF members."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import xarray as xr


VARIABLES = ("t2m", "d2m", "u10", "v10", "tp", "ssrd")
TOLERANCES = {
    # Both products are decoded to float32 from GRIB, but global and CDS
    # sub-area packing can quantize the decoded fields on slightly different
    # binary steps. These bounds remain below 0.001 K, 0.0001 m/s,
    # 0.003 mm precipitation and 1 J/m2 radiation respectively.
    "t2m": 5e-4,
    "d2m": 5e-4,
    "u10": 1e-4,
    "v10": 1e-4,
    "tp": 3e-6,
    "ssrd": 1.0,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aws", type=Path, required=True)
    parser.add_argument("--cds-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def canonical_aws(path: Path) -> xr.Dataset:
    dataset = xr.open_dataset(path, engine="h5netcdf").load()
    longitude = ((dataset.longitude + 180.0) % 360.0) - 180.0
    return (
        dataset.assign_coords(longitude=longitude)
        .sortby("longitude")
        .sortby("latitude", ascending=False)
    )


def canonical_cds(directory: Path) -> xr.Dataset:
    instant_path = next(directory.glob("*instant.nc"))
    accum_path = next(directory.glob("*accum.nc"))
    with xr.open_dataset(instant_path, engine="h5netcdf") as instant:
        loaded_instant = instant.load()
    with xr.open_dataset(accum_path, engine="h5netcdf") as accum:
        loaded_accum = accum.load()
    return xr.merge([loaded_instant, loaded_accum], join="exact", compat="override").sortby(
        "latitude", ascending=False
    )


def main() -> int:
    args = parse_args()
    aws = canonical_aws(args.aws)
    cds = canonical_cds(args.cds_dir)
    coordinate_results = {
        coordinate: {
            "aws_count": int(aws.sizes[coordinate]),
            "cds_count": int(cds.sizes[coordinate]),
            "exact": bool(np.array_equal(aws[coordinate].values, cds[coordinate].values)),
            "max_abs_difference": float(
                np.max(np.abs(aws[coordinate].values - cds[coordinate].values))
            ),
        }
        for coordinate in ("valid_time", "latitude", "longitude")
    }
    variable_results: dict[str, object] = {}
    for name in VARIABLES:
        left = aws[name].values
        right = cds[name].values
        if left.shape != right.shape:
            raise ValueError(f"{name}: shape mismatch {left.shape} != {right.shape}")
        difference = np.abs(left.astype(np.float64) - right.astype(np.float64))
        tolerance = TOLERANCES[name]
        variable_results[name] = {
            "shape": list(left.shape),
            "aws_dtype": str(left.dtype),
            "cds_dtype": str(right.dtype),
            "aws_units": aws[name].attrs.get("units"),
            "cds_units": cds[name].attrs.get("units"),
            "exact_fraction": float(np.mean(left == right)),
            "maximum_absolute_difference": float(difference.max()),
            "mean_absolute_difference": float(difference.mean()),
            "absolute_tolerance": tolerance,
            "within_tolerance": bool(np.all(difference <= tolerance)),
            "aws_step_type": aws[name].attrs.get("GRIB_stepType"),
            "cds_step_type": cds[name].attrs.get("GRIB_stepType"),
        }
    passed = all(item["exact"] for item in coordinate_results.values()) and all(
        bool(item["within_tolerance"]) for item in variable_results.values()  # type: ignore[union-attr]
    )
    report = {
        "status": "equivalent" if passed else "different",
        "aws_path": str(args.aws),
        "cds_directory": str(args.cds_dir),
        "coordinates": coordinate_results,
        "variables": variable_results,
        "accumulation_semantics": {
            "tp": "one-hour accumulation ending at valid_time, metres of water equivalent",
            "ssrd": "one-hour accumulation ending at valid_time, joules per square metre",
            "comparison": "values compared directly without differencing or temporal summation",
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_name(f".{args.report.name}.part")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
