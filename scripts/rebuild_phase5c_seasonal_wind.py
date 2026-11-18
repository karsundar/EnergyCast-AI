#!/usr/bin/env python3
"""Build versioned seasonal ensembles with ERA5-consistent scalar wind aggregation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
WEIGHTS = ROOT / "data/processed/weather/geospatial/ercot_seasonal_1deg_grid_weights.parquet"
REGIONAL = ROOT / "data/processed/weather/seasonal/regional"
OUTPUT = ROOT / "data/processed/weather/seasonal/phase5c_corrected"
PROVIDERS = {
    "seas5": ("copernicus_seas5", "seas5_member_monthly.parquet"),
    "ncep": ("copernicus_cfsv2", "ncep_cfsv2_member_monthly.parquet"),
}
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


def rebuild(provider: str, source_name: str, member_name: str) -> dict[str, object]:
    started = time.monotonic()
    source = ROOT / "data/processed/weather/seasonal/bulk" / source_name
    member = REGIONAL / member_name
    connection = duckdb.connect()
    connection.execute("SET threads=8")
    connection.execute("SET memory_limit='8GB'")
    connection.execute(f"CREATE VIEW source AS SELECT * FROM read_parquet('{source}/*/year=*/*.parquet')")
    connection.execute(f"CREATE VIEW weights AS SELECT * FROM read_parquet('{WEIGHTS}')")
    connection.execute(f"CREATE VIEW existing AS SELECT * FROM read_parquet('{member}')")
    connection.execute("""
        CREATE TEMP VIEW scalar_wind AS
        SELECT s.issue_date, s.target_month, s.lead_month, s.ensemble_member,
               w.weather_zone, SUM(s.wind_speed_mps * w.zone_weight) AS wind_speed_mps
        FROM source s JOIN weights w USING (latitude, longitude)
        GROUP BY ALL
    """)
    expressions: list[str] = []
    for variable in VARIABLES:
        source_expression = "w.wind_speed_mps" if variable == "wind_speed_mps" else f"m.{variable}"
        expressions.extend([
            f"AVG({source_expression}) AS {variable}_mean",
            f"QUANTILE_CONT({source_expression}, 0.1) AS {variable}_p10",
            f"QUANTILE_CONT({source_expression}, 0.5) AS {variable}_p50",
            f"QUANTILE_CONT({source_expression}, 0.9) AS {variable}_p90",
        ])
    ensemble = connection.execute(f"""
        SELECT m.issue_date, m.available_at, m.target_month, m.lead_month, m.weather_zone,
               ANY_VALUE(m.forecast_kind) AS forecast_kind,
               ANY_VALUE(m.source_provider) AS source_provider,
               ANY_VALUE(m.forecast_system) AS forecast_system,
               ANY_VALUE(m.availability_kind) AS availability_kind,
               COUNT(DISTINCT m.ensemble_member) AS ensemble_member_count,
               {', '.join(expressions)}
        FROM existing m JOIN scalar_wind w
        USING (issue_date, target_month, lead_month, ensemble_member, weather_zone)
        GROUP BY m.issue_date, m.available_at, m.target_month, m.lead_month, m.weather_zone
        ORDER BY m.issue_date, m.lead_month, m.weather_zone
    """).df()
    if len(ensemble) != 12_258:
        raise RuntimeError(f"unexpected corrected {provider} ensemble rows: {len(ensemble)}")
    output = OUTPUT / f"{provider}_ensemble_monthly.parquet"
    atomic_parquet(ensemble, output)
    return {"provider": provider, "rows": len(ensemble), "output": str(output),
            "runtime_seconds": time.monotonic() - started,
            "wind_definition": "ensemble distribution of area-weighted grid-cell scalar wind speed"}


def main() -> int:
    reports = [rebuild(provider, *values) for provider, values in PROVIDERS.items()]
    report = {"status": "validated", "phase5b_artifacts_modified": False, "providers": reports}
    destination = ROOT / "reports/phase5c/wind_aggregation_correction.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
