#!/usr/bin/env python3
"""Run Phase 5C Stage 1 seasonal weather skill evaluation without model training."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time

import duckdb
import numpy as np
import pandas as pd

from weather.seasonal_skill import (
    KEYS, add_expanding_normals, chronological_bias_correct, metric_rows,
    validate_forecast_keys, validation_weights, weighted_provider_ensemble,
)

ROOT = Path(__file__).resolve().parents[1]


def atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".parquet", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary, index=False, compression="zstd")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_inputs(config: dict[str, object], smoke: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    frames = []
    for provider, relative in config["providers"].items():
        frame = pd.read_parquet(ROOT / relative)
        frame["provider"] = provider
        frame["track"] = np.where(frame.forecast_kind.eq("archived_realtime_forecast"),
                                  "archived", "hindcast_research")
        frames.append(frame)
    forecasts = pd.concat(frames, ignore_index=True)
    forecasts["target_month"] = pd.to_datetime(forecasts.target_month).dt.to_period("M").dt.to_timestamp()
    if smoke:
        archived = forecasts[forecasts.track.eq("archived") & forecasts.target_month.le("2022-06-01")]
        hindcast = forecasts[forecasts.track.eq("hindcast_research") & forecasts.target_month.ge("2014-01-01")]
        forecasts = pd.concat([archived, hindcast], ignore_index=True)
    history = duckdb.sql(
        f"SELECT * FROM read_parquet('{ROOT / config['era5_glob']}')"
    ).df().rename(columns={"month": "target_month"})
    history["target_month"] = (
        pd.to_datetime(history.target_month, utc=True).dt.tz_localize(None).dt.to_period("M").dt.to_timestamp()
    )
    return forecasts, history


def build_evaluation(config: dict[str, object], smoke: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    forecasts, history = load_inputs(config, smoke)
    validate_forecast_keys(forecasts)
    variables = config["variables"]
    actual = history[["target_month", "weather_zone", *variables]]
    frame = forecasts.merge(actual, on=["target_month", "weather_zone"], how="inner", validate="many_to_one")
    if frame.empty:
        raise RuntimeError("no seasonal forecasts overlap ERA5 observations")
    for variable in variables:
        frame = frame.rename(columns={
            variable: f"{variable}_actual", f"{variable}_mean": f"{variable}_raw",
        })
    frame = add_expanding_normals(frame, history, variables, int(config["minimum_normal_years"]))
    for variable in variables:
        frame = chronological_bias_correct(
            frame, variable, int(config["minimum_bias_samples"]),
            int(config["era5_publication_lag_days"]),
        )
    target = frame.target_month
    archived = frame.track.eq("archived")
    hindcast = frame.track.eq("hindcast_research")
    frame["period"] = np.select([
        archived & target.between(config["validation_start"], config["validation_end"]),
        archived & target.between(config["test_start"], config["test_end"]),
        hindcast & target.between(config["hindcast_validation_start"], config["hindcast_validation_end"]),
        hindcast & target.between(config["hindcast_test_start"], config["hindcast_test_end"]),
    ], ["validation", "test", "hindcast_research_validation", "hindcast_research_test"],
       default="development_research")
    rows: list[dict[str, object]] = []
    for variable in variables:
        rows.extend(metric_rows(frame, variable, "raw"))
        rows.extend(metric_rows(frame, variable, "calibrated"))
        normal = f"{variable}_normal"
        frame[f"{variable}_climate_normal"] = frame[normal]
        rows.extend(metric_rows(frame, variable, "climate_normal"))
    metrics = pd.DataFrame(rows)
    return frame, metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config/phase5c_weather_skill.json")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--output-label", help="versioned output directory; defaults to smoke or full")
    options = parser.parse_args()
    started = time.monotonic()
    config = json.loads(options.config.read_text())
    frame, metrics = build_evaluation(config, options.smoke_test)
    weights = validation_weights(metrics)
    ensemble = weighted_provider_ensemble(frame, weights, config["variables"])
    ensemble_metric_rows: list[dict[str, object]] = []
    for variable in config["variables"]:
        subset = ensemble[ensemble.variable.eq(variable)].copy()
        if subset.empty:
            continue
        subset["provider"] = "validation_weighted_ensemble"
        subset["geography_scope"] = np.where(subset.weather_zone.eq("SYSTEM"), "system", "regional")
        ensemble_metric_rows.extend(metric_rows(subset, variable, "calibrated"))
    if ensemble_metric_rows:
        metrics = pd.concat([metrics, pd.DataFrame(ensemble_metric_rows)], ignore_index=True)
    label = options.output_label or ("smoke" if options.smoke_test else "full")
    output = ROOT / config["output_root"] / label
    atomic_parquet(frame, output / "weather_skill_predictions.parquet")
    atomic_parquet(metrics, output / "weather_skill_metrics.parquet")
    atomic_parquet(weights, output / "validation_provider_weights.parquet")
    atomic_parquet(ensemble, output / "validation_weighted_ensemble.parquet")
    archive = frame[frame.track.eq("archived")]
    hindcast = frame[frame.track.eq("hindcast_research")]
    report = {
        "status": "smoke_passed" if options.smoke_test else "complete",
        "stage": "Phase 5C Stage 1 only",
        "rows": len(frame), "metric_rows": len(metrics),
        "archived_rows": len(archive), "hindcast_research_rows": len(hindcast),
        "archive_target_coverage": [str(archive.target_month.min().date()), str(archive.target_month.max().date())],
        "hindcast_target_coverage": [str(hindcast.target_month.min().date()), str(hindcast.target_month.max().date())],
        "leads": sorted(frame.lead_month.unique().tolist()),
        "providers": sorted(frame.provider.unique().tolist()),
        "operational_availability_missing": int(archive.available_at.isna().sum()),
        "hindcast_availability_nonnull": int(hindcast.available_at.notna().sum()),
        "archive_gap_fabricated_rows_2017_2020": int(archive.target_month.between("2017-01-01", "2020-12-01").sum()),
        "weights_source": "archived validation or separate hindcast-research validation only; inverse MAE by track/variable/lead/geography scope",
        "hindcast_warning": "retrospective research only; no historical real-time availability asserted",
        "availability_evidence": "provider scheduled operational release; not file-specific archive audit timestamp",
        "humidity_target": "relative_humidity_pct; ERA5 regional monthly has no dewpoint target",
        "runtime_seconds": time.monotonic() - started,
        "outputs": {
            "predictions": str(output / "weather_skill_predictions.parquet"),
            "metrics": str(output / "weather_skill_metrics.parquet"),
            "weights": str(output / "validation_provider_weights.parquet"),
            "ensemble": str(output / "validation_weighted_ensemble.parquet"),
        },
    }
    report_path = output / "validation.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    os.replace(temporary, report_path)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
