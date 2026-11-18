#!/usr/bin/env python3
"""Aggregate validated SEAS5 grid partitions and add leakage-safe normal fallback."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "processed" / "weather" / "seasonal" / "bulk" / "copernicus_seas5"
WEIGHTS = ROOT / "data" / "processed" / "weather" / "geospatial" / "ercot_seasonal_1deg_grid_weights.parquet"
REGIONAL = ROOT / "data" / "processed" / "weather" / "seasonal" / "regional"
HISTORY_GLOB = str(ROOT / "data" / "processed" / "weather" / "regional" / "monthly" / "year=*" / "part.parquet")
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


def climate_fallback(issue_dates: list[pd.Timestamp]) -> pd.DataFrame:
    history = duckdb.sql(f"SELECT * FROM read_parquet('{HISTORY_GLOB}')").df()
    history["month"] = pd.to_datetime(history["month"]).dt.to_period("M").dt.to_timestamp()
    records: list[dict[str, object]] = []
    for raw_issue in issue_dates:
        issue = pd.Timestamp(raw_issue).tz_localize(None).to_period("M").to_timestamp()
        available = history[history.month < issue]
        for zone, group in available.groupby("weather_zone", observed=True):
            for lead in range(7, 13):
                # CDS forecastMonth=1 verifies in the initialization month.
                target = issue + pd.DateOffset(months=lead - 1)
                matching = group[group.month.dt.month == target.month]
                if matching.month.dt.year.nunique() < 10:
                    continue
                record: dict[str, object] = {
                    "issue_date": issue, "available_at": issue,
                    "target_month": target, "lead_month": lead,
                    "weather_zone": zone, "forecast_kind": "climate_normal_fallback",
                    "source_provider": "era5_expanding_normal",
                    "forecast_system": "climate_normal_not_dynamical_forecast",
                    "availability_kind": "known_at_forecast_origin",
                    "ensemble_member_count": 0,
                }
                for variable in VARIABLES:
                    value = float(matching[variable].mean()) if variable in matching else np.nan
                    record[f"{variable}_mean"] = value
                    record[f"{variable}_p10"] = np.nan
                    record[f"{variable}_p50"] = np.nan
                    record[f"{variable}_p90"] = np.nan
                records.append(record)
    return pd.DataFrame(records)


def main() -> int:
    started = time.monotonic()
    REGIONAL.mkdir(parents=True, exist_ok=True)
    member_path = REGIONAL / "seas5_member_monthly.parquet"
    ensemble_path = REGIONAL / "seas5_ensemble_monthly.parquet"
    operational_path = REGIONAL / "seas5_operational_12month.parquet"
    connection = duckdb.connect()
    connection.execute("SET threads=8")
    connection.execute("SET memory_limit='8GB'")
    source_glob = str(SOURCE / "*" / "year=*" / "*.parquet")
    connection.execute(f"CREATE VIEW source AS SELECT * FROM read_parquet('{source_glob}', filename=true)")
    connection.execute(f"CREATE VIEW weights AS SELECT * FROM read_parquet('{WEIGHTS}')")
    member = connection.execute("""
        SELECT
            s.issue_date, s.available_at, s.target_month, s.lead_month,
            s.ensemble_member, w.weather_zone,
            SUM(s.temperature_mean_c * w.zone_weight) AS temperature_mean_c,
            SUM(s.dewpoint_mean_c * w.zone_weight) AS dewpoint_mean_c,
            SUM(s.relative_humidity_pct * w.zone_weight) AS relative_humidity_pct,
            SUM(s.u10 * w.zone_weight) AS wind_u_mps,
            SUM(s.v10 * w.zone_weight) AS wind_v_mps,
            SQRT(POW(SUM(s.u10 * w.zone_weight), 2) + POW(SUM(s.v10 * w.zone_weight), 2)) AS wind_speed_mps,
            SUM(s.precipitation_mm * w.zone_weight) AS precipitation_mm,
            SUM(s.solar_energy_mj_m2 * w.zone_weight) AS solar_energy_mj_m2,
            ANY_VALUE(s.forecast_kind) AS forecast_kind,
            ANY_VALUE(s.source_provider) AS source_provider,
            ANY_VALUE(s.forecast_system) AS forecast_system,
            ANY_VALUE(s.availability_kind) AS availability_kind,
            STRING_AGG(DISTINCT s.filename, ';') AS source_partitions,
            COUNT(*) AS contributing_weight_rows
        FROM source s
        JOIN weights w USING (latitude, longitude)
        GROUP BY ALL
        ORDER BY issue_date, lead_month, ensemble_member, weather_zone
    """).df()
    atomic_parquet(member, member_path)
    connection.execute(f"CREATE VIEW member_view AS SELECT * FROM read_parquet('{member_path}')")
    expressions = []
    for variable in VARIABLES:
        expressions.extend([
            f"AVG({variable}) AS {variable}_mean",
            f"QUANTILE_CONT({variable}, 0.1) AS {variable}_p10",
            f"QUANTILE_CONT({variable}, 0.5) AS {variable}_p50",
            f"QUANTILE_CONT({variable}, 0.9) AS {variable}_p90",
        ])
    ensemble = connection.execute(f"""
        SELECT issue_date, available_at, target_month, lead_month, weather_zone,
               ANY_VALUE(forecast_kind) AS forecast_kind,
               ANY_VALUE(source_provider) AS source_provider,
               ANY_VALUE(forecast_system) AS forecast_system,
               ANY_VALUE(availability_kind) AS availability_kind,
               COUNT(DISTINCT ensemble_member) AS ensemble_member_count,
               {', '.join(expressions)}
        FROM member_view
        GROUP BY issue_date, available_at, target_month, lead_month, weather_zone
        ORDER BY issue_date, lead_month, weather_zone
    """).df()
    atomic_parquet(ensemble, ensemble_path)
    forecasts = ensemble[ensemble.forecast_kind == "archived_realtime_forecast"].copy()
    issue_dates = sorted(pd.to_datetime(forecasts.issue_date, utc=True).unique())
    fallback = climate_fallback(issue_dates)
    fallback["issue_date"] = pd.to_datetime(fallback["issue_date"], utc=True)
    fallback["available_at"] = pd.to_datetime(fallback["available_at"], utc=True)
    forecasts["issue_date"] = pd.to_datetime(forecasts["issue_date"], utc=True)
    forecasts["available_at"] = pd.to_datetime(forecasts["available_at"], utc=True)
    operational = pd.concat([forecasts, fallback], ignore_index=True, sort=False)
    operational = operational.sort_values(["issue_date", "lead_month", "weather_zone"]).reset_index(drop=True)
    atomic_parquet(operational, operational_path)

    member_keys = ["issue_date", "lead_month", "ensemble_member", "weather_zone"]
    ensemble_keys = ["issue_date", "lead_month", "weather_zone"]
    validation = {
        "status": "validated",
        "source_provider": "ecmwf_seas5_system_51",
        "member_rows": len(member), "ensemble_rows": len(ensemble),
        "operational_rows": len(operational), "climate_fallback_rows": len(fallback),
        "member_duplicates": int(member.duplicated(member_keys).sum()),
        "ensemble_duplicates": int(ensemble.duplicated(ensemble_keys).sum()),
        "issue_coverage": [
            pd.Timestamp(ensemble.issue_date.min()).tz_convert("UTC").isoformat(),
            pd.Timestamp(ensemble.issue_date.max()).tz_convert("UTC").isoformat(),
        ],
        "target_coverage": [str(ensemble.target_month.min()), str(ensemble.target_month.max())],
        "hindcast_member_counts": sorted(member.loc[member.forecast_kind == "retrospective_hindcast"].groupby("issue_date")["ensemble_member"].nunique().unique().tolist()),
        "forecast_member_counts": sorted(member.loc[member.forecast_kind == "archived_realtime_forecast"].groupby("issue_date")["ensemble_member"].nunique().unique().tolist()),
        "hindcast_available_at_nonnull": int(member.loc[member.forecast_kind == "retrospective_hindcast", "available_at"].notna().sum()),
        "operational_leads": sorted(operational.lead_month.unique().tolist()),
        "operational_duplicate_keys": int(operational.duplicated(ensemble_keys).sum()),
        "dynamic_forecast_rows": len(forecasts),
        "numeric_nulls": {column: int(ensemble[column].isna().sum()) for column in ensemble.select_dtypes(include="number")},
        "mapping_approximate": True,
        "daily_extremes_fabricated": False,
        "climate_fallback_unavailable_fields": ["dewpoint_mean_c"],
        "runtime_seconds": time.monotonic() - started,
        "outputs": {"member": str(member_path), "ensemble": str(ensemble_path), "operational": str(operational_path)},
    }
    if (validation["member_duplicates"] or validation["ensemble_duplicates"]
            or validation["operational_duplicate_keys"] or validation["hindcast_available_at_nonnull"]
            or validation["operational_leads"] != list(range(1, 13))):
        validation["status"] = "invalid"
    report = ROOT / "reports" / "phase5b_seas5_processing_validation.json"
    report.write_text(json.dumps(validation, indent=2) + "\n")
    print(json.dumps(validation, indent=2))
    return 0 if validation["status"] == "validated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
