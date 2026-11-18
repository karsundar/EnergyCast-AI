#!/usr/bin/env python3
"""Partitioned DuckDB aggregation and weather-demand integration for Phase 3B."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
WEATHER = ROOT / "data/processed/weather"
OUT = WEATHER / "regional"
WEIGHTS = WEATHER / "geospatial/ercot_weather_zone_grid_weights.parquet"


def sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def copy_query(connection: duckdb.DuckDBPyConnection, query: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.parquet")
    connection.execute(
        f"COPY ({query}) TO '{sql_path(temporary)}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)"
    )
    temporary.replace(output)


def aggregate_year(connection: duckdb.DuckDBPyConnection, year: int) -> dict[str, int]:
    weights = sql_path(WEIGHTS)
    hourly_input = sql_path(WEATHER / f"hourly_grid/year={year}/*.parquet")
    daily_input = sql_path(WEATHER / f"daily_grid/year={year}/*.parquet")
    hourly_output = OUT / f"hourly/year={year}/part.parquet"
    daily_output = OUT / f"daily/year={year}/part.parquet"
    monthly_output = OUT / f"monthly/year={year}/part.parquet"

    hourly_query = f"""
      SELECT h.timestamp_utc, h.timestamp_local, w.weather_zone,
        SUM(h.temperature_c*w.zone_weight)/SUM(w.zone_weight) AS temperature_mean_c,
        MIN(h.temperature_c) AS temperature_min_c,
        MAX(h.temperature_c) AS temperature_max_c,
        SUM(h.temperature_f*w.zone_weight)/SUM(w.zone_weight) AS temperature_mean_f,
        SUM(h.relative_humidity_pct*w.zone_weight)/SUM(w.zone_weight) AS relative_humidity_pct,
        SUM(h.wind_speed_mps*w.zone_weight)/SUM(w.zone_weight) AS wind_speed_mps,
        SUM(h.precipitation_mm*w.zone_weight)/SUM(w.zone_weight) AS precipitation_mm,
        SUM(h.solar_radiation_j_m2*w.zone_weight)/SUM(w.zone_weight) AS solar_radiation_j_m2,
        SUM(w.intersection_area_m2) AS represented_area_m2,
        COUNT(*) AS contributing_grid_cells,
        'secondary_academic_county_crosswalk' AS mapping_provenance,
        TRUE AS mapping_approximate
      FROM read_parquet('{hourly_input}') h
      JOIN read_parquet('{weights}') w USING(latitude, longitude)
      GROUP BY h.timestamp_utc, h.timestamp_local, w.weather_zone
    """
    copy_query(connection, hourly_query, hourly_output)

    daily_base = f"""
      SELECT CAST(d.local_date AS DATE) AS date, w.weather_zone,
        SUM(d.temperature_mean_c*w.zone_weight)/SUM(w.zone_weight) AS temperature_mean_c,
        MIN(d.temperature_min_c) AS temperature_min_c,
        MAX(d.temperature_max_c) AS temperature_max_c,
        SUM(d.relative_humidity_mean_pct*w.zone_weight)/SUM(w.zone_weight) AS relative_humidity_pct,
        SUM(d.wind_speed_mean_mps*w.zone_weight)/SUM(w.zone_weight) AS wind_speed_mps,
        SUM(d.precipitation_total_mm*w.zone_weight)/SUM(w.zone_weight) AS precipitation_mm,
        SUM(d.solar_energy_total_mj_m2*w.zone_weight)/SUM(w.zone_weight) AS solar_energy_mj_m2,
        SUM(d.hdd_65f*w.zone_weight)/SUM(w.zone_weight) AS hdd_65f,
        SUM(d.cdd_65f*w.zone_weight)/SUM(w.zone_weight) AS cdd_65f,
        MIN(d.completeness_pct) AS completeness_pct,
        SUM(w.intersection_area_m2) AS represented_area_m2,
        COUNT(*) AS contributing_grid_cells
      FROM read_parquet('{daily_input}') d
      JOIN read_parquet('{weights}') w USING(latitude, longitude)
      GROUP BY CAST(d.local_date AS DATE), w.weather_zone
    """
    daily_query = f"""
      WITH base AS ({daily_base}), flags AS (
        SELECT *, temperature_max_c >= 37.7777778 AS extreme_heat_day,
          temperature_min_c <= 0.0 AS freezing_day
        FROM base
      ), runs AS (
        SELECT *,
          SUM(CASE WHEN extreme_heat_day THEN 1 ELSE 0 END) OVER
            (PARTITION BY weather_zone ORDER BY date ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS heat_3day_count,
          SUM(CASE WHEN freezing_day THEN 1 ELSE 0 END) OVER
            (PARTITION BY weather_zone ORDER BY date ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS cold_3day_count
        FROM flags
      )
      SELECT *, heat_3day_count=3 AS heatwave_3day, cold_3day_count=3 AS coldwave_3day,
        'secondary_academic_county_crosswalk' AS mapping_provenance,
        TRUE AS mapping_approximate
      FROM runs
    """
    copy_query(connection, daily_query, daily_output)

    monthly_query = f"""
      SELECT DATE_TRUNC('month', date) AS month, weather_zone,
        AVG(temperature_mean_c) AS temperature_mean_c,
        MIN(temperature_min_c) AS temperature_min_c,
        MAX(temperature_max_c) AS temperature_max_c,
        AVG(relative_humidity_pct) AS relative_humidity_pct,
        AVG(wind_speed_mps) AS wind_speed_mps,
        SUM(precipitation_mm) AS precipitation_mm,
        SUM(solar_energy_mj_m2) AS solar_energy_mj_m2,
        SUM(hdd_65f) AS hdd_65f, SUM(cdd_65f) AS cdd_65f,
        SUM(CAST(extreme_heat_day AS INTEGER)) AS extreme_heat_days,
        SUM(CAST(freezing_day AS INTEGER)) AS freezing_days,
        SUM(CAST(heatwave_3day AS INTEGER)) AS heatwave_days,
        SUM(CAST(coldwave_3day AS INTEGER)) AS coldwave_days,
        MIN(completeness_pct) AS completeness_pct,
        MAX(represented_area_m2) AS represented_area_m2,
        MAX(contributing_grid_cells) AS contributing_grid_cells,
        'secondary_academic_county_crosswalk' AS mapping_provenance,
        TRUE AS mapping_approximate
      FROM read_parquet('{sql_path(daily_output)}')
      GROUP BY DATE_TRUNC('month', date), weather_zone
    """
    copy_query(connection, monthly_query, monthly_output)
    return {
        "hourly": connection.execute(f"SELECT COUNT(*) FROM read_parquet('{sql_path(hourly_output)}')").fetchone()[0],
        "daily": connection.execute(f"SELECT COUNT(*) FROM read_parquet('{sql_path(daily_output)}')").fetchone()[0],
        "monthly": connection.execute(f"SELECT COUNT(*) FROM read_parquet('{sql_path(monthly_output)}')").fetchone()[0],
    }


def integrate_demand(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    demand = sql_path(ROOT / "data/processed/ercot_hourly.parquet")
    hourly_weather = sql_path(OUT / "hourly/year=*/part.parquet")
    daily_weather = sql_path(OUT / "daily/year=*/part.parquet")
    monthly_weather = sql_path(OUT / "monthly/year=*/part.parquet")
    integrated = OUT / "integrated"
    demand_cte = f"""
      WITH regional AS (SELECT timestamp_utc, timestamp_local, weather_zone, demand_mw,
        interval_duration_hours, energy_mwh, quality_flag FROM read_parquet('{demand}')
        WHERE timestamp_utc < TIMESTAMPTZ '2026-06-01'),
      system AS (SELECT timestamp_utc, MIN(timestamp_local) timestamp_local, 'SYSTEM' weather_zone,
        SUM(demand_mw) demand_mw, MAX(interval_duration_hours) interval_duration_hours,
        SUM(energy_mwh) energy_mwh,
        CASE WHEN COUNT(*)=8 THEN 'complete' ELSE 'incomplete' END quality_flag
        FROM regional GROUP BY timestamp_utc),
      demand_all AS (SELECT * FROM regional UNION ALL SELECT * FROM system)
    """
    hourly_query = demand_cte + f"""
      SELECT d.*, w.temperature_mean_c, w.temperature_min_c, w.temperature_max_c,
        w.temperature_mean_f, w.relative_humidity_pct, w.wind_speed_mps,
        w.precipitation_mm, w.solar_radiation_j_m2, w.mapping_provenance, w.mapping_approximate
      FROM demand_all d JOIN read_parquet('{hourly_weather}') w
      USING(timestamp_utc, weather_zone)
    """
    copy_query(connection, hourly_query, integrated / "weather_demand_hourly.parquet")
    daily_demand = demand_cte + """
      SELECT CAST(timestamp_local AS DATE) date, weather_zone,
        SUM(energy_mwh) total_energy_mwh,
        SUM(demand_mw*interval_duration_hours)/SUM(interval_duration_hours) average_demand_mw,
        MAX(demand_mw) peak_demand_mw, MIN(demand_mw) minimum_demand_mw,
        COUNT(*) observation_count
      FROM demand_all GROUP BY CAST(timestamp_local AS DATE), weather_zone
    """
    daily_query = f"""WITH d AS ({daily_demand}) SELECT d.*, w.* EXCLUDE(date, weather_zone)
      FROM d JOIN read_parquet('{daily_weather}') w USING(date, weather_zone)"""
    copy_query(connection, daily_query, integrated / "weather_demand_daily.parquet")
    monthly_demand = demand_cte + """
      SELECT DATE_TRUNC('month', timestamp_local) AS "month", weather_zone,
        SUM(energy_mwh) total_energy_mwh,
        SUM(demand_mw*interval_duration_hours)/SUM(interval_duration_hours) average_demand_mw,
        MAX(demand_mw) peak_demand_mw, MIN(demand_mw) minimum_demand_mw,
        COUNT(*) observation_count
      FROM demand_all GROUP BY DATE_TRUNC('month', timestamp_local), weather_zone
    """
    monthly_query = f"""WITH d AS ({monthly_demand}) SELECT d.*, w.* EXCLUDE(month, weather_zone)
      FROM d JOIN read_parquet('{monthly_weather}') w USING(month, weather_zone)"""
    copy_query(connection, monthly_query, integrated / "weather_demand_monthly.parquet")
    return {
        name: connection.execute(f"SELECT COUNT(*) FROM read_parquet('{sql_path(integrated / f'weather_demand_{name}.parquet')}')").fetchone()[0]
        for name in ("hourly", "daily", "monthly")
    }


def exploratory_report(connection: duckdb.DuckDBPyConnection) -> None:
    integrated = OUT / "integrated"
    monthly = sql_path(integrated / "weather_demand_monthly.parquet")
    daily = sql_path(integrated / "weather_demand_daily.parquet")
    summary = connection.execute(f"""
      SELECT weather_zone, COUNT(*) AS month_count,
        CORR(temperature_mean_c,total_energy_mwh) temperature_energy_correlation,
        CORR(cdd_65f,total_energy_mwh) cdd_energy_correlation,
        CORR(hdd_65f,total_energy_mwh) hdd_energy_correlation,
        CORR(temperature_max_c,peak_demand_mw) temperature_peak_correlation
      FROM read_parquet('{monthly}') GROUP BY weather_zone ORDER BY weather_zone
    """).fetchdf()
    extremes = connection.execute(f"""
      SELECT weather_zone,
        AVG(peak_demand_mw) FILTER (WHERE extreme_heat_day) heat_extreme_average_peak_mw,
        AVG(peak_demand_mw) FILTER (WHERE freezing_day) freeze_average_peak_mw,
        SUM(CAST(heatwave_3day AS INTEGER)) heatwave_days,
        SUM(CAST(coldwave_3day AS INTEGER)) coldwave_days
      FROM read_parquet('{daily}') GROUP BY weather_zone ORDER BY weather_zone
    """).fetchdf()
    report = summary.merge(extremes, on="weather_zone")
    report.to_csv(ROOT / "reports/phase3b_weather_demand_statistics.csv", index=False)
    charts = ROOT / "reports/charts/phase3b"
    charts.mkdir(parents=True, exist_ok=True)
    sample = connection.execute(f"""SELECT temperature_mean_c,total_energy_mwh,weather_zone
      FROM read_parquet('{monthly}') WHERE weather_zone!='SYSTEM'""").fetchdf()
    fig, ax = plt.subplots(figsize=(9, 6))
    for zone, group in sample.groupby("weather_zone"):
        ax.scatter(group.temperature_mean_c, group.total_energy_mwh, s=7, alpha=.35, label=zone)
    ax.set(xlabel="Monthly mean temperature (°C)", ylabel="Monthly energy (MWh)", title="ERCOT weather-zone temperature and demand")
    ax.legend(ncol=2, fontsize=7)
    fig.tight_layout()
    fig.savefig(charts / "temperature_vs_energy.png", dpi=160)
    plt.close(fig)
    (ROOT / "reports/phase3b_exploratory_analysis.md").write_text(
        "# Phase 3B weather-demand exploratory analysis\n\n"
        "Associations below are descriptive and are not causal estimates. Weather uses an "
        "explicitly approximate secondary county mapping.\n\n```csv\n"
        + report.to_csv(index=False) + "```\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2004)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--memory-limit", default="12GB")
    parser.add_argument("--aggregate-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    connection = duckdb.connect()
    connection.execute(f"SET threads={args.threads}")
    connection.execute(f"SET memory_limit='{args.memory_limit}'")
    connection.execute(f"SET temp_directory='{sql_path(ROOT / 'data/interim/duckdb_phase3b')}'")
    results: dict[str, object] = {"years": {}}
    for year in range(args.start_year, args.end_year + 1):
        output = OUT / f"hourly/year={year}/part.parquet"
        if output.exists() and not args.force:
            results["years"][str(year)] = "cache_hit"
        else:
            results["years"][str(year)] = aggregate_year(connection, year)
        print(f"completed {year}", flush=True)
    if not args.aggregate_only and args.start_year == 2004 and args.end_year == 2026:
        results["integrated"] = integrate_demand(connection)
        exploratory_report(connection)
    results["elapsed_seconds"] = time.monotonic() - started
    (ROOT / "reports/phase3b_processing_summary.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
