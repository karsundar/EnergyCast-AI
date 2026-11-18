#!/usr/bin/env python3
"""Process a frozen snapshot of validated CDS NCEP seasonal partitions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "phase5b_ncep_bulk.json"
WEIGHTS = ROOT / "data/processed/weather/geospatial/ercot_seasonal_1deg_grid_weights.parquet"
DEFAULT_OUTPUT = ROOT / "data/processed/weather/seasonal/regional"
VARIABLES = [
    "temperature_mean_c", "dewpoint_mean_c", "relative_humidity_pct",
    "wind_speed_mps", "precipitation_mm", "solar_energy_mj_m2",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".parquet", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary, index=False, compression="zstd")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(value: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(temporary, path)


def validated_snapshot(progress: dict[str, Any], verify_checksums: bool = True) -> list[Path]:
    """Freeze only ledger-validated partitions; ignore an active writer's future entries."""
    paths: list[Path] = []
    for key, entry in sorted(progress.get("entries", {}).items()):
        if entry.get("status") != "validated" or not key.startswith("copernicus_cfsv2:"):
            continue
        raw = Path(entry["raw_path"])
        processed = Path(entry["processed_path"])
        if not raw.is_file() or not processed.is_file():
            raise RuntimeError(f"validated ledger entry is missing files: {key}")
        if verify_checksums and sha256(raw) != entry.get("sha256"):
            raise RuntimeError(f"raw checksum mismatch: {key}")
        paths.append(processed.resolve())
    return paths


def sql_path_list(paths: list[Path]) -> str:
    return "[" + ",".join("'" + str(path).replace("'", "''") + "'" for path in paths) + "]"


def process(paths: list[Path], output_root: Path) -> dict[str, Any]:
    started = time.monotonic()
    connection = duckdb.connect()
    connection.execute("SET threads=8")
    connection.execute("SET memory_limit='8GB'")
    connection.execute(f"CREATE VIEW source AS SELECT * FROM read_parquet({sql_path_list(paths)}, filename=true)")
    connection.execute(f"CREATE VIEW weights AS SELECT * FROM read_parquet('{WEIGHTS}')")
    source_rows, matched_rows = connection.execute("""
        SELECT (SELECT COUNT(*) FROM source),
               (SELECT COUNT(*) FROM source JOIN weights USING (latitude, longitude))
    """).fetchone()
    expected_matches = source_rows * 200 // 154
    if matched_rows != expected_matches:
        raise RuntimeError(
            f"seasonal grid/weight mismatch: {matched_rows=} {expected_matches=} {source_rows=}"
        )
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
    member_path = output_root / "ncep_cfsv2_member_monthly.parquet"
    ensemble_path = output_root / "ncep_cfsv2_ensemble_monthly.parquet"
    atomic_parquet(member, member_path)
    connection.execute(f"CREATE VIEW member_view AS SELECT * FROM read_parquet('{member_path}')")
    expressions: list[str] = []
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
    member_keys = ["issue_date", "lead_month", "ensemble_member", "weather_zone"]
    ensemble_keys = ["issue_date", "lead_month", "weather_zone"]
    counts = sorted(member.groupby("issue_date").ensemble_member.nunique().unique().tolist())
    report: dict[str, Any] = {
        "status": "validated",
        "input_partitions": len(paths),
        "source_rows": int(source_rows),
        "matched_weight_rows": int(matched_rows),
        "member_rows": len(member),
        "ensemble_rows": len(ensemble),
        "member_counts_by_initialization": counts,
        "member_duplicates": int(member.duplicated(member_keys).sum()),
        "ensemble_duplicates": int(ensemble.duplicated(ensemble_keys).sum()),
        "hindcast_available_at_nonnull": int(member.loc[
            member.forecast_kind == "retrospective_hindcast", "available_at"
        ].notna().sum()),
        "lead_months": sorted(member.lead_month.unique().tolist()),
        "issue_coverage": [
            pd.Timestamp(member.issue_date.min()).tz_convert("UTC").isoformat(),
            pd.Timestamp(member.issue_date.max()).tz_convert("UTC").isoformat(),
        ],
        "runtime_seconds": time.monotonic() - started,
        "outputs": {"member": str(member_path), "ensemble": str(ensemble_path)},
    }
    if (report["member_duplicates"] or report["ensemble_duplicates"]
            or report["hindcast_available_at_nonnull"] or report["lead_months"] != list(range(1, 7))):
        report["status"] = "invalid"
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-incomplete", action="store_true", help="bounded processing smoke test")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=ROOT / "reports/phase5b_ncep_processing_validation.json")
    options = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    progress = json.loads((ROOT / config["progress_path"]).read_text())
    paths = validated_snapshot(progress)
    expected = 227
    if len(paths) != expected and not options.allow_incomplete:
        raise RuntimeError(
            f"NCEP acquisition is incomplete ({len(paths)}/{expected}); rerun after bulk validation "
            "or use --allow-incomplete with a temporary output directory for a bounded smoke test"
        )
    if not paths:
        raise RuntimeError("no validated NCEP partitions available")
    report = process(paths, options.output_root.resolve())
    report["acquisition_complete"] = len(paths) == expected
    report["expected_partitions"] = expected
    atomic_json(report, options.report.resolve())
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "validated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
