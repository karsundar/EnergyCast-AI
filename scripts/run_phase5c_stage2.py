#!/usr/bin/env python3
"""Phase 5C Stage 2 gated operational-weather electricity experiments."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

import duckdb
import numpy as np
import pandas as pd

from forecasting.operational_weather import (
    TARGETS, WEATHER_FEATURES, assert_e18_leakage_safe, assert_gate_b_leakage_safe,
    fit_operational_model, fit_predict_e18, historical_samples, model_columns,
    normal_queries, predict_operational, provider_queries,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/phase5c_stage2.json"


def atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".parquet", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary, index=False, compression="zstd")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_inputs(config: dict[str, object]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    demand = pd.read_parquet(ROOT / config["demand_targets"])
    demand["month"] = pd.to_datetime(demand.month).dt.to_period("M").dt.to_timestamp()
    historical = duckdb.sql(
        f"SELECT * FROM read_parquet('{ROOT / config['historical_weather_glob']}')"
    ).df()
    historical["target_month"] = (
        pd.to_datetime(historical.target_month, utc=True).dt.tz_localize(None).dt.to_period("M").dt.to_timestamp()
    )
    historical["geography"] = historical.weather_zone.replace({"SYSTEM": "ERCOT_SYSTEM"})
    operational = pd.read_parquet(ROOT / config["operational_weather_features"])
    operational["target_month"] = pd.to_datetime(operational.target_month).dt.to_period("M").dt.to_timestamp()
    seasonal = pd.read_parquet(ROOT / config["raw_seasonal_weather"])
    seasonal["target_month"] = pd.to_datetime(seasonal.target_month).dt.to_period("M").dt.to_timestamp()
    if set(config["weather_features"]) != set(WEATHER_FEATURES):
        raise ValueError("Stage 2 weather feature schema differs from the validated five-variable schema")
    forbidden = {"hdd", "cdd", "extreme", "heatwave", "coldwave", "minimum", "maximum"}
    if any(any(token in str(value).lower() for token in forbidden) for value in operational.feature.unique()):
        raise ValueError("operational feature table contains unsupported derived/daily weather")
    return demand, historical, operational, seasonal


def metric_table(predictions: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    valid = predictions[predictions.forecast_status.eq("complete")].copy()
    valid["horizon_band"] = np.where(valid.horizon.le(6), "1-6", "7-12")
    month = valid.target_month.dt.month
    valid["season"] = np.select(
        [month.isin([12, 1, 2]), month.isin([3, 4, 5]), month.isin([6, 7, 8])],
        ["winter", "spring", "summer"], default="fall",
    )
    for breakdown, columns in {
        "overall": ["experiment", "target_name"],
        "horizon_band": ["experiment", "target_name", "horizon_band"],
        "horizon": ["experiment", "target_name", "horizon"],
        "geography": ["experiment", "target_name", "geography"],
        "season": ["experiment", "target_name", "season"],
        "geography_horizon_band": ["experiment", "target_name", "geography", "horizon_band"],
        "geography_horizon": ["experiment", "target_name", "geography", "horizon"],
        "geography_season": ["experiment", "target_name", "geography", "season"],
    }.items():
        for keys, group in valid.groupby(columns, observed=True):
            keys = keys if isinstance(keys, tuple) else (keys,)
            error = group.p50 - group.actual
            rows.append({
                "breakdown": breakdown, **dict(zip(columns, keys, strict=True)),
                "observations": len(group), "mae": float(error.abs().mean()),
                "rmse": float(np.sqrt(np.mean(np.square(error)))),
                "wape": float(error.abs().sum() / group.actual.abs().sum() * 100),
                "signed_bias": float(error.mean()),
            })
    return pd.DataFrame(rows)


def weather_difference_audit(reference: pd.DataFrame, candidate: pd.DataFrame, experiment: str) -> dict[str, object]:
    order = ["geography", "target_month", "horizon"]
    left = reference.sort_values(order).reset_index(drop=True)
    right = candidate.sort_values(order).reset_index(drop=True)
    if not left[order].equals(right[order]):
        raise ValueError(f"{experiment} query keys differ from E18")
    weather = [f"weather_{name}" for name in WEATHER_FEATURES]
    unrelated = [column for column in model_columns(left) if column not in weather]
    unrelated_difference = np.nanmax(np.abs(
        left[unrelated].to_numpy(dtype=float) - right[unrelated].to_numpy(dtype=float)
    ))
    dynamic = right.horizon.le(6).to_numpy()
    weather_difference = np.abs(
        left[weather].to_numpy(dtype=float) - right[weather].to_numpy(dtype=float)
    )
    return {
        "experiment": experiment,
        "rows": len(right),
        "unrelated_feature_max_abs_difference": float(unrelated_difference),
        "dynamic_weather_cells_changed": int((weather_difference[dynamic] > 0).sum()),
        "dynamic_weather_cells": int(weather_difference[dynamic].size),
        "fallback_weather_max_abs_difference": float(np.nanmax(weather_difference[~dynamic])) if (~dynamic).any() else 0.0,
    }


def gate_b_common(predictions: pd.DataFrame, config: dict[str, object]) -> tuple[pd.DataFrame, pd.DataFrame]:
    e18 = pd.read_parquet(ROOT / "data/processed/phase5c_stage2/gate_a/E18_predictions.parquet")
    chronos = pd.read_parquet(ROOT / config["baseline_predictions"]["E0_E11"])
    e0 = chronos[(chronos.experiment.eq("E0")) & (chronos.target_name.eq("peak_demand_mw"))]
    e14 = pd.read_parquet(ROOT / config["baseline_predictions"]["E14"])
    e14 = e14[e14.target_name.eq("total_energy_mwh")]
    all_predictions = pd.concat([e18, predictions, e0, e14], ignore_index=True, sort=False)
    if all_predictions.oracle_weather.fillna(False).any():
        raise ValueError("Gate B comparison includes oracle predictions")
    keys = ["target_name", "origin", "target_month", "horizon", "geography"]
    expected = {"total_energy_mwh": {"E14", "E18", "E19", "E20"},
                "peak_demand_mw": {"E0", "E18", "E19", "E20"}}
    common_frames = []
    for target, experiments in expected.items():
        subset = all_predictions[
            all_predictions.target_name.eq(target) & all_predictions.experiment.isin(experiments)
        ]
        common = None
        for experiment in experiments:
            experiment_keys = set(map(tuple, subset.loc[subset.experiment.eq(experiment), keys].to_numpy()))
            common = experiment_keys if common is None else common & experiment_keys
        selected = subset[subset[keys].apply(tuple, axis=1).isin(common)]
        counts = selected.groupby(keys, observed=True).experiment.nunique()
        if not counts.eq(4).all():
            raise ValueError(f"incomplete common comparison for {target}")
        common_frames.append(selected)
    comparable = pd.concat(common_frames, ignore_index=True, sort=False)
    spread = comparable.groupby(keys, observed=True).actual.agg(lambda values: values.max() - values.min())
    if (spread > 1e-6).any():
        raise ValueError("Gate B actual values disagree")
    return comparable, metric_table(comparable)


def common_benchmarks(e18: pd.DataFrame, config: dict[str, object]) -> tuple[pd.DataFrame, pd.DataFrame]:
    chronos = pd.read_parquet(ROOT / config["baseline_predictions"]["E0_E11"])
    chronos = chronos[chronos.experiment.isin(["E0", "E11"])]
    ensemble = pd.read_parquet(ROOT / config["baseline_predictions"]["E14"])
    baselines = pd.concat([chronos, ensemble], ignore_index=True, sort=False)
    if baselines.oracle_weather.fillna(False).any():
        raise ValueError("Gate A benchmark includes oracle predictions")
    keys = ["target_name", "origin", "target_month", "horizon", "geography"]
    e18_keys = pd.MultiIndex.from_frame(e18[keys])
    baseline_keys = pd.MultiIndex.from_frame(baselines[keys])
    baselines = baselines[baseline_keys.isin(e18_keys)]
    common = set(map(tuple, e18[keys].to_numpy()))
    for experiment in ("E0", "E11", "E14"):
        common &= set(map(tuple, baselines.loc[baselines.experiment.eq(experiment), keys].to_numpy()))
    comparable = pd.concat([
        e18[e18[keys].apply(tuple, axis=1).isin(common)],
        baselines[baselines[keys].apply(tuple, axis=1).isin(common)],
    ], ignore_index=True, sort=False)
    actual_spread = comparable.groupby(keys, observed=True).actual.agg(lambda x: x.max() - x.min())
    if (actual_spread > 1e-6).any():
        raise ValueError("common-observation actual values disagree")
    counts = comparable.groupby(keys, observed=True).experiment.nunique()
    if not counts.eq(4).all():
        raise ValueError("common benchmark does not contain E0/E11/E14/E18 for every key")
    return comparable, metric_table(comparable)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate", choices=["A", "B"], default="A")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--evaluate-only", action="store_true")
    parser.add_argument("--start-origin")
    parser.add_argument("--end-origin")
    parser.add_argument("--max-horizon", type=int)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    options = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    label = (f"gate_{options.gate.lower()}_smoke" if options.smoke_test
             else f"gate_{options.gate.lower()}")
    # Retain the original Gate A smoke location for backward compatibility.
    if options.gate == "A" and options.smoke_test:
        label = "smoke"
    output = ROOT / "data/processed/phase5c_stage2" / label
    reports = ROOT / "reports/phase5c_stage2" / label
    partition_dir = output / "predictions"
    partition_dir.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    audits: list[dict[str, object]] = []
    if not options.evaluate_only:
        demand, historical, operational, seasonal = load_inputs(config)
        start = options.start_origin or config["start_origin"]
        end = options.end_origin or config["end_origin"]
        horizon = int(options.max_horizon or config["maximum_horizon"])
        origins = pd.date_range(start, end, freq="MS")
        if options.smoke_test:
            origins, horizon = origins[:1], min(horizon, 2)
            demand = demand[demand.geography.eq("ERCOT_SYSTEM")]
            historical = historical[historical.geography.eq("ERCOT_SYSTEM")]
        samples = {target: historical_samples(demand, historical, target, horizon) for target in TARGETS}
        for origin in origins:
            for target in TARGETS:
                if options.gate == "A":
                    destination = partition_dir / f"E18_{target}_{origin:%Y%m}.parquet"
                    if options.resume and destination.exists():
                        continue
                    queries = normal_queries(demand, operational, origin, target, horizon)
                    predictions = fit_predict_e18(
                        demand, samples[target], queries, target, origin, config["lightgbm"],
                    )
                    assert_e18_leakage_safe(predictions)
                    atomic_parquet(predictions, destination)
                    print(f"completed E18:{target}:{origin:%Y-%m} rows={len(predictions)}", flush=True)
                    continue

                destinations = {
                    experiment: partition_dir / f"{experiment}_{target}_{origin:%Y%m}.parquet"
                    for experiment in ("E19", "E20")
                }
                if options.resume and all(path.exists() for path in destinations.values()):
                    continue
                reference = normal_queries(demand, operational, origin, target, horizon)
                model, cutoff = fit_operational_model(samples[target], origin, config["lightgbm"])
                reference_prediction = predict_operational(
                    model, model_columns(samples[target]), demand, reference, target, origin, "E18", cutoff,
                )
                reference_path = ROOT / "data/processed/phase5c_stage2" / (
                    "smoke" if options.smoke_test else "gate_a"
                ) / "predictions" / f"E18_{target}_{origin:%Y%m}.parquet"
                if not reference_path.exists():
                    raise RuntimeError(f"missing Gate A reference partition: {reference_path}")
                saved_reference = pd.read_parquet(reference_path).sort_values(
                    ["geography", "target_month", "horizon"]
                )
                reproduced = reference_prediction.sort_values(
                    ["geography", "target_month", "horizon"]
                )
                reproduction_delta = float(np.max(np.abs(
                    saved_reference.p50.to_numpy() - reproduced.p50.to_numpy()
                )))
                if reproduction_delta > 1e-9:
                    raise ValueError(f"Gate B fitted model does not reproduce E18: {reproduction_delta}")
                for experiment, provider in (("E19", "seas5"), ("E20", "ncep")):
                    if options.resume and destinations[experiment].exists():
                        continue
                    queries = provider_queries(
                        demand, operational, seasonal, origin, target, horizon, provider,
                    )
                    audit = weather_difference_audit(reference, queries, experiment)
                    audit.update({"target_name": target, "origin": origin,
                                  "reference_prediction_max_abs_difference": reproduction_delta})
                    audits.append(audit)
                    predictions = predict_operational(
                        model, model_columns(samples[target]), demand, queries, target,
                        origin, experiment, cutoff,
                    )
                    assert_gate_b_leakage_safe(predictions)
                    atomic_parquet(predictions, destinations[experiment])
                    print(f"completed {experiment}:{target}:{origin:%Y-%m} rows={len(predictions)}", flush=True)
        if options.gate == "B" and audits:
            atomic_parquet(pd.DataFrame(audits), reports / "weather_feature_difference_audit.parquet")

    experiment_names = ["E18"] if options.gate == "A" else ["E19", "E20"]
    parts = [path for experiment in experiment_names
             for path in sorted(partition_dir.glob(f"{experiment}_*.parquet"))]
    if not parts:
        raise RuntimeError(f"no Gate {options.gate} partitions found")
    predictions = pd.concat([pd.read_parquet(path) for path in parts], ignore_index=True)
    if options.gate == "A":
        assert_e18_leakage_safe(predictions)
        comparable, metrics = common_benchmarks(predictions, config)
        atomic_parquet(predictions, output / "E18_predictions.parquet")
        common_name, metric_name = "gate_a_common_predictions.parquet", "gate_a_common_metrics.csv"
    else:
        assert_gate_b_leakage_safe(predictions)
        comparable, metrics = gate_b_common(predictions, config)
        atomic_parquet(predictions[predictions.experiment.eq("E19")], output / "E19_predictions.parquet")
        atomic_parquet(predictions[predictions.experiment.eq("E20")], output / "E20_predictions.parquet")
        common_name, metric_name = "gate_b_common_predictions.parquet", "gate_b_common_metrics.csv"
    atomic_parquet(comparable, output / common_name)
    metrics.to_csv(reports / metric_name, index=False)
    report = {
        "status": "smoke_passed" if options.smoke_test else "complete",
        "gate": options.gate, "implemented_experiments": experiment_names,
        "deferred_experiments": (["E19", "E20", "E21", "E22", "E23"]
                                 if options.gate == "A" else ["E21", "E22", "E23"]),
        "prediction_rows": len(predictions), "common_rows": len(comparable),
        "common_experiments": sorted(comparable.experiment.unique().tolist()),
        "oracle_rows": int(comparable.oracle_weather.fillna(False).sum()),
        "stage2_weather_schema": list(WEATHER_FEATURES),
        "daily_or_extreme_features": False,
        "model_policy": "rolling refit per target/origin; one fixed fitted model shared by E19 and E20",
        "outputs": {"common_predictions": str(output / common_name),
                    "metrics": str(reports / metric_name)},
    }
    (reports / f"gate_{options.gate.lower()}_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
