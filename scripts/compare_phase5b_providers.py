#!/usr/bin/env python3
"""Compare complete regional SEAS5 and NCEP seasonal products on common keys."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REGIONAL = ROOT / "data/processed/weather/seasonal/regional"
VARIABLES = [
    "temperature_mean_c", "dewpoint_mean_c", "relative_humidity_pct",
    "wind_speed_mps", "precipitation_mm", "solar_energy_mj_m2",
]


def atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".parquet", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary, index=False, compression="zstd")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(value: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-incomplete", action="store_true", help="compare bounded common partitions")
    parser.add_argument("--output-root", type=Path, default=REGIONAL)
    parser.add_argument("--report", type=Path, default=ROOT / "reports/phase5b_provider_comparison.json")
    parser.add_argument("--ncep-report", type=Path, default=ROOT / "reports/phase5b_ncep_processing_validation.json")
    options = parser.parse_args()
    ncep_report_path = options.ncep_report
    if not ncep_report_path.exists():
        raise RuntimeError("run process_phase5b_ncep.py after NCEP acquisition")
    ncep_report = json.loads(ncep_report_path.read_text())
    if not ncep_report.get("acquisition_complete") and not options.allow_incomplete:
        raise RuntimeError("NCEP processing is bounded/incomplete; full comparison is intentionally blocked")
    seas5 = pd.read_parquet(REGIONAL / "seas5_ensemble_monthly.parquet")
    ncep = pd.read_parquet(Path(ncep_report["outputs"]["ensemble"]))
    keys = ["issue_date", "target_month", "lead_month", "weather_zone", "forecast_kind"]
    columns = keys + [f"{variable}_mean" for variable in VARIABLES]
    common = seas5[columns].merge(ncep[columns], on=keys, suffixes=("_seas5", "_ncep"), validate="one_to_one")
    if common.empty:
        raise RuntimeError("providers have no common regional forecast keys")
    summary: dict[str, object] = {}
    for variable in VARIABLES:
        left = common[f"{variable}_mean_seas5"]
        right = common[f"{variable}_mean_ncep"]
        delta = right - left
        summary[variable] = {
            "rows": len(delta),
            "ncep_minus_seas5_mean": float(delta.mean()),
            "mae": float(delta.abs().mean()),
            "rmse": float(np.sqrt(np.mean(np.square(delta)))),
            "correlation": float(left.corr(right)),
        }
        common[f"{variable}_multi_provider_mean"] = (left + right) / 2
    common["provider_count"] = 2
    common["ensemble_method"] = "equal_provider_mean_uncalibrated"
    output = options.output_root.resolve() / "seas5_ncep_common_provider_features.parquet"
    atomic_parquet(common, output)
    report = {
        "status": "validated",
        "comparison_scope": "complete" if ncep_report.get("acquisition_complete") else "bounded_incomplete",
        "common_rows": len(common),
        "common_issue_coverage": [
            pd.Timestamp(common.issue_date.min()).tz_convert("UTC").isoformat(),
            pd.Timestamp(common.issue_date.max()).tz_convert("UTC").isoformat(),
        ],
        "metrics": summary,
        "output": str(output),
        "ensemble_warning": "equal-provider mean is an uncalibrated feature, not a validated forecast ensemble",
    }
    atomic_json(report, options.report.resolve())
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
