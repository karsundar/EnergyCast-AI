#!/usr/bin/env python3
"""Build monthly ERA5 references matching seasonal temporal/nonlinear definitions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import duckdb

ROOT = Path(__file__).resolve().parents[1]
HOURLY = ROOT / "data/processed/weather/hourly_grid"
WEIGHTS = ROOT / "data/processed/weather/geospatial/ercot_weather_zone_grid_weights.parquet"
OUTPUT = ROOT / "data/processed/weather/seasonal_compatible_era5"


def sql(path: Path) -> str:
    return str(path).replace("'", "''")


def build_year(year: int, overwrite: bool = False) -> dict[str, object]:
    destination = OUTPUT / f"year={year}/part.parquet"
    if destination.exists() and not overwrite:
        return {"year": year, "status": "cached", "path": str(destination)}
    source = HOURLY / f"year={year}/*.parquet"
    if not list((HOURLY / f"year={year}").glob("*.parquet")):
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp.parquet")
    started = time.monotonic()
    connection = duckdb.connect()
    connection.execute("SET threads=8")
    connection.execute("SET memory_limit='8GB'")
    query = f"""
      WITH grid AS (
        SELECT DATE_TRUNC('month', timestamp_utc) AS target_month, latitude, longitude,
          AVG(temperature_c) AS temperature_mean_c,
          AVG(dewpoint_c) AS dewpoint_mean_c,
          AVG(wind_u_mps) AS wind_u_monthly_mean_mps,
          AVG(wind_v_mps) AS wind_v_monthly_mean_mps,
          SUM(precipitation_mm) AS precipitation_mm,
          SUM(solar_radiation_mj_m2) AS solar_energy_mj_m2,
          COUNT(*) AS observation_count
        FROM read_parquet('{sql(source)}')
        WHERE EXTRACT(year FROM timestamp_utc) = {year}
        GROUP BY ALL
      ), derived AS (
        SELECT *,
          SQRT(POW(wind_u_monthly_mean_mps, 2) + POW(wind_v_monthly_mean_mps, 2)) AS wind_speed_mps,
          LEAST(100.0, GREATEST(0.0, 100.0 * EXP(
            (17.625 * dewpoint_mean_c) / (243.04 + dewpoint_mean_c)
            - (17.625 * temperature_mean_c) / (243.04 + temperature_mean_c)
          ))) AS relative_humidity_pct
        FROM grid
      )
      SELECT d.target_month, w.weather_zone,
        SUM(d.temperature_mean_c*w.zone_weight)/SUM(w.zone_weight) AS temperature_mean_c,
        SUM(d.dewpoint_mean_c*w.zone_weight)/SUM(w.zone_weight) AS dewpoint_mean_c,
        SUM(d.relative_humidity_pct*w.zone_weight)/SUM(w.zone_weight) AS relative_humidity_pct,
        SUM(d.wind_speed_mps*w.zone_weight)/SUM(w.zone_weight) AS wind_speed_mps,
        SUM(d.precipitation_mm*w.zone_weight)/SUM(w.zone_weight) AS precipitation_mm,
        SUM(d.solar_energy_mj_m2*w.zone_weight)/SUM(w.zone_weight) AS solar_energy_mj_m2,
        MIN(d.observation_count) AS minimum_grid_observations,
        'ERA5 hourly -> UTC monthly grid means -> nonlinear transform -> area weighted region' AS aggregation_order,
        'temperature/dewpoint C; wind m/s; precipitation mm/month; solar MJ/m2/month; RH percent' AS units
      FROM derived d JOIN read_parquet('{sql(WEIGHTS)}') w USING(latitude, longitude)
      GROUP BY d.target_month, w.weather_zone
      ORDER BY d.target_month, w.weather_zone
    """
    connection.execute(
        f"COPY ({query}) TO '{sql(temporary)}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    temporary.replace(destination)
    rows, months, zones, minimum = connection.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT target_month), COUNT(DISTINCT weather_zone), "
        f"MIN(minimum_grid_observations) FROM read_parquet('{sql(destination)}')"
    ).fetchone()
    if zones != 9 or rows != months * zones or minimum < 24 * 28:
        raise RuntimeError(f"invalid comparable ERA5 partition for {year}: {rows=}, {months=}, {zones=}, {minimum=}")
    return {"year": year, "status": "validated", "rows": rows, "months": months,
            "zones": zones, "minimum_grid_observations": minimum, "path": str(destination),
            "seconds": time.monotonic() - started}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2004)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--overwrite", action="store_true")
    options = parser.parse_args()
    reports = [build_year(year, options.overwrite) for year in range(options.start_year, options.end_year + 1)]
    report = {"status": "validated", "years": reports,
              "phase5b_artifacts_modified": False, "canonical_era5_modified": False}
    path = ROOT / "reports/phase5c_stage15/era5_compatible_build.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
