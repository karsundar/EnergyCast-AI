#!/usr/bin/env python3
"""Build bounded normalized SEAS5 samples and leakage-safe climate normals."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.weather.seasonal import climate_normal_forecast, normalize_seas5_sample


def main() -> int:
    raw = ROOT / "data" / "raw" / "weather" / "seasonal"
    output = ROOT / "data" / "processed" / "weather" / "seasonal"
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for kind, label in (("hindcast", "retrospective_hindcast"),
                        ("forecast", "archived_realtime_forecast")):
        source = raw / f"seas5_{kind}_sample.zip"
        frame = normalize_seas5_sample(source, label)
        destination = output / f"seas5_{kind}_sample.parquet"
        frame.to_parquet(destination, index=False, compression="zstd")
        artifacts[kind] = {"path": str(destination), "rows": len(frame), "bytes": destination.stat().st_size}

    for kind, label in (("hindcast", "retrospective_hindcast"),
                        ("forecast", "archived_realtime_forecast")):
        source = raw / f"ncep_cfsv2_{kind}_sample.nc"
        frame = normalize_seas5_sample(source, label, "copernicus_cfsv2")
        destination = output / f"ncep_cfsv2_{kind}_sample.parquet"
        frame.to_parquet(destination, index=False, compression="zstd")
        artifacts[f"ncep_cfsv2_{kind}"] = {
            "path": str(destination), "rows": len(frame), "bytes": destination.stat().st_size,
        }

    history_path = ROOT / "data" / "processed" / "weather" / "regional" / "integrated" / "weather_demand_monthly.parquet"
    history = pd.read_parquet(history_path)
    weather_columns = [
        "temperature_mean_c", "temperature_min_c", "temperature_max_c", "relative_humidity_pct",
        "wind_speed_mps", "precipitation_mm", "solar_energy_mj_m2", "hdd_65f", "cdd_65f",
    ]
    origins = list(pd.date_range("2018-01-01", "2026-06-01", freq="MS"))
    normals = climate_normal_forecast(history, origins, weather_columns, minimum_years=10)
    normal_path = output / "climate_normal_monthly.parquet"
    normals.to_parquet(normal_path, index=False, compression="zstd")
    artifacts["climate_normal"] = {"path": str(normal_path), "rows": len(normals),
                                   "bytes": normal_path.stat().st_size,
                                   "first_issue": str(normals.issue_date.min().date()),
                                   "last_issue": str(normals.issue_date.max().date())}
    report = {"status": "passed", "artifacts": artifacts}
    report_path = ROOT / "reports" / "phase5a_processed_samples.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
