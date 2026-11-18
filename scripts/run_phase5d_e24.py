#!/usr/bin/env python3
"""Resumable E24 growth and statistical-baseline log-ratio evaluation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

import duckdb
import numpy as np
import pandas as pd

from forecasting.e24 import (
    assert_e24_leakage_safe, build_log_ratio_samples, e24_feature_columns,
    fit_log_ratio_model_at, query_features, reconstruct, select_multiplicative_lambda,
)
from forecasting.e25 import attach_expanding_normals
from forecasting.operational_weather import TARGETS, normal_queries

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/phase5d_e24.json"
E0_PATH = ROOT / "data/processed/phase4c/chronos_test_predictions_comparable.parquet"


def atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".parquet", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary, index=False, compression="zstd")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    demand = pd.read_parquet(ROOT / "data/processed/ercot_monthly_targets.parquet")
    demand["month"] = pd.to_datetime(demand.month).dt.to_period("M").dt.to_timestamp()
    historical = duckdb.sql(
        f"SELECT * FROM read_parquet('{ROOT}/data/processed/weather/seasonal_compatible_era5/year=*/part.parquet')"
    ).df()
    historical["target_month"] = (
        pd.to_datetime(historical.target_month, utc=True).dt.tz_localize(None)
        .dt.to_period("M").dt.to_timestamp()
    )
    operational = pd.read_parquet(
        ROOT / "reports/phase5c_stage15/full/operational_weather_features.parquet"
    )
    operational["target_month"] = pd.to_datetime(operational.target_month).dt.to_period("M").dt.to_timestamp()
    e0 = pd.read_parquet(E0_PATH)
    e0 = e0[e0.experiment.eq("E0")].copy()
    for column in ("origin", "target_month"):
        e0[column] = pd.to_datetime(e0[column])
    return demand, historical, operational, e0


def training_samples(
    demand: pd.DataFrame, historical: pd.DataFrame, target: str,
    config: dict[str, object], reference: str,
) -> pd.DataFrame:
    source = ROOT / "data/processed/phase5d" / f"e25_oos_residuals_{target}.parquet"
    if not source.exists():
        raise RuntimeError(f"validated extended OOS Holt-Winters cache missing: {source}")
    raw = pd.read_parquet(source)
    raw["origin"] = pd.to_datetime(raw.origin)
    raw["target_month"] = pd.to_datetime(raw.target_month)
    normals = attach_expanding_normals(raw, historical, int(config["minimum_normal_years"]))
    target_demand = demand[["geography", "month", target]].rename(columns={target: "value"})
    return build_log_ratio_samples(
        raw, target_demand, normals, reference,
        int(config["target_publication_lag_days"]),
    )


def make_prediction(
    experiment: str, model: object, columns: list[str], shrinkage: float,
    features: pd.DataFrame, target: str, issuance: pd.Timestamp,
    training_cutoff: pd.Timestamp, reference_type: str,
) -> pd.DataFrame:
    predicted_ratio = model.predict(features[columns])
    prediction = reconstruct(features.reference_value.to_numpy(), predicted_ratio, shrinkage)
    keep = [
        "origin", "target_month", "horizon", "geography", "actual", "reference_value",
        "reference_month", "weather_provider", "weather_available_at", "fallback_reason", "training_max",
    ]
    result = features[keep].copy()
    result["experiment"] = experiment
    result["model"] = "lightgbm_multiplicative_growth"
    result["target_name"] = target
    result["p50"] = prediction
    result["predicted_log_ratio"] = predicted_ratio
    result["shrinkage_lambda"] = shrinkage
    result["reference_type"] = reference_type
    result["label_training_cutoff"] = training_cutoff
    result["operational_forecast_time"] = issuance
    result["issuance_schedule"] = "month_start"
    result["oracle_weather"] = False
    result["forecast_status"] = np.where(result.actual.notna(), "complete", "incomplete_horizon")
    result["actual_above_training_max"] = result.actual > result.training_max
    result["prediction_above_training_max"] = result.p50 > result.training_max
    assert_e24_leakage_safe(result)
    return result


def calculate_metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    frame = predictions[predictions.forecast_status.eq("complete")].copy()
    frame["horizon_band"] = np.where(frame.horizon.le(6), "1-6", "7-12")
    rows = []
    dimensions = {
        "overall": [], "horizon": ["horizon"], "horizon_band": ["horizon_band"],
        "region": ["geography"], "region_horizon_band": ["geography", "horizon_band"],
    }
    for breakdown, extra in dimensions.items():
        groups = ["experiment", "target_name", *extra]
        for keys, group in frame.groupby(groups, observed=True):
            keys = keys if isinstance(keys, tuple) else (keys,)
            error = group.p50 - group.actual
            rows.append({
                "breakdown": breakdown, **dict(zip(groups, keys, strict=True)),
                "observations": len(group),
                "wape": float(error.abs().sum() / group.actual.abs().sum() * 100),
                "mae": float(error.abs().mean()),
                "rmse": float(np.sqrt(np.square(error).mean())),
                "signed_bias": float(error.mean()),
            })
    return pd.DataFrame(rows)


def paired_uncertainty(frame: pd.DataFrame, samples: int) -> pd.DataFrame:
    keys = ["target_name", "origin", "target_month", "horizon", "geography"]
    work = frame.copy()
    work["absolute_error"] = (work.p50 - work.actual).abs()
    work["horizon_band"] = np.where(work.horizon.le(6), "1-6", "7-12")
    wide = work.pivot(index=keys, columns="experiment", values=["absolute_error", "actual"])
    bands = work[keys + ["horizon_band"]].drop_duplicates().set_index(keys)
    rng = np.random.default_rng(42)
    rows = []
    candidates = ("E24-A", "E24-B", "E24-C")
    references = ("E0", "E9", "E14", "E25-A", "E25-B", "E25-C")
    for candidate in candidates:
        for reference in references:
            if ("absolute_error", reference) not in wide:
                continue
            pair = pd.DataFrame({
                "delta": wide[("absolute_error", candidate)] - wide[("absolute_error", reference)],
                "actual": wide[("actual", candidate)],
            }).join(bands).reset_index()
            for scope, scoped in (("all", pair), ("system", pair[pair.geography.eq("ERCOT_SYSTEM")])):
                for (target, band), group in scoped.groupby(["target_name", "horizon_band"], observed=True):
                    count = len(group)
                    indices = rng.integers(0, count, size=(samples, count))
                    delta = group.delta.to_numpy()
                    denominator = group.actual.abs().to_numpy()
                    boot = delta[indices].sum(axis=1) / denominator[indices].sum(axis=1) * 100
                    rows.append({
                        "candidate": candidate, "reference": reference, "scope": scope,
                        "target_name": target, "horizon_band": band, "observations": count,
                        "wape_difference": float(delta.sum() / denominator.sum() * 100),
                        "wape_difference_ci_low": float(np.quantile(boot, .025)),
                        "wape_difference_ci_high": float(np.quantile(boot, .975)),
                    })
    return pd.DataFrame(rows)


def common_controls() -> list[pd.DataFrame]:
    e0 = pd.read_parquet(E0_PATH)
    e0 = e0[e0.experiment.eq("E0")]
    e9 = pd.read_parquet(ROOT / "data/processed/phase4b/phase4b_predictions_comparable.parquet")
    e9 = e9[e9.experiment.eq("E9")]
    e14 = pd.read_parquet(ROOT / "data/processed/phase4b/E14_predictions_non-oracle.parquet")
    e25 = pd.read_parquet(ROOT / "data/processed/phase5d/e25/full/E25_predictions.parquet")
    for frame in (e0, e9, e14, e25):
        frame["origin"] = pd.to_datetime(frame.origin)
        frame["target_month"] = pd.to_datetime(frame.target_month)
    return [e0, e9, e14, e25]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--evaluate-only", action="store_true")
    parser.add_argument("--start-origin")
    parser.add_argument("--end-origin")
    parser.add_argument("--max-horizon", type=int)
    parser.add_argument("--bootstrap-samples", type=int)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    options = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    label = "smoke" if options.smoke_test else "full"
    output = ROOT / "data/processed/phase5d/e24" / label
    report_root = ROOT / "reports/phase5d/e24" / label
    partitions = output / "predictions"
    partitions.mkdir(parents=True, exist_ok=True)
    report_root.mkdir(parents=True, exist_ok=True)
    selections: list[dict[str, object]] = []

    if not options.evaluate_only:
        demand, historical, operational, e0 = load_inputs()
        origins = pd.date_range(options.start_origin or config["start_origin"],
                                options.end_origin or config["end_origin"], freq="MS")
        horizon = int(options.max_horizon or config["maximum_horizon"])
        query_demand = demand
        if options.smoke_test:
            origins, horizon = origins[:1], min(horizon, 2)
            query_demand = demand[demand.geography.eq("ERCOT_SYSTEM")]
        samples: dict[tuple[str, str], pd.DataFrame] = {}
        lambdas: dict[tuple[str, str], float] = {}
        for target in TARGETS:
            for reference in ("seasonal_naive", "holt_winters_oos"):
                sample = training_samples(demand, historical, target, config, reference)
                selected, scores = select_multiplicative_lambda(
                    sample, config["lightgbm"], config["lambda_grid"],
                    pd.Timestamp(config["development_training_end"]),
                    pd.Timestamp(config["development_validation_start"]),
                    pd.Timestamp(config["development_validation_end"]),
                )
                samples[(target, reference)] = sample
                lambdas[(target, reference)] = selected
                selections.append({
                    "target_name": target, "reference_type": reference,
                    "selected_lambda": selected, "development_wape": scores,
                })
        for origin in origins:
            for target in TARGETS:
                destinations = {
                    name: partitions / f"{name}_{target}_{origin:%Y%m}.parquet"
                    for name in ("E24-A", "E24-B", "E24-C")
                }
                if options.resume and all(path.exists() for path in destinations.values()):
                    continue
                target_baselines = e0[
                    e0.target_name.eq(target) & e0.origin.eq(origin)
                    & e0.horizon.le(horizon)
                    & e0.geography.isin(query_demand.geography.unique())
                ]
                weather = normal_queries(query_demand, operational, origin, target, horizon)
                issuance = (origin + pd.offsets.MonthBegin(1)).tz_localize("UTC")
                if (pd.to_datetime(weather.weather_available_at, utc=True) > issuance).any():
                    raise ValueError("climate normal unavailable at month-start issuance")
                fitted = {}
                for reference in ("seasonal_naive", "holt_winters_oos"):
                    fitted[reference] = fit_log_ratio_model_at(
                        samples[(target, reference)], issuance, config["lightgbm"]
                    )
                specifications = {
                    "E24-A": ("seasonal_naive", 1.0),
                    "E24-B": ("holt_winters_oos", lambdas[(target, "holt_winters_oos")]),
                    "E24-C": ("seasonal_naive", lambdas[(target, "seasonal_naive")]),
                }
                for experiment, (reference, shrinkage) in specifications.items():
                    feature = query_features(
                        query_demand, weather, target_baselines, target, origin, reference
                    )
                    model, cutoff = fitted[reference]
                    prediction = make_prediction(
                        experiment, model, e24_feature_columns(samples[(target, reference)]),
                        shrinkage, feature, target, issuance, cutoff, reference,
                    )
                    atomic_parquet(prediction, destinations[experiment])
                    print(f"completed {experiment}:{target}:{origin:%Y-%m} rows={len(prediction)}", flush=True)
        (report_root / "lambda_selection.json").write_text(json.dumps(selections, indent=2) + "\n")

    paths = sorted(partitions.glob("E24-*.parquet"))
    if not paths:
        raise RuntimeError("no E24 prediction partitions found")
    predictions = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
    assert_e24_leakage_safe(predictions)
    keys = ["target_name", "origin", "target_month", "horizon", "geography"]
    controls = common_controls()
    common = set(map(tuple, predictions[keys].to_numpy()))
    for frame in controls:
        common &= set(map(tuple, frame[keys].to_numpy()))
    frames = [predictions, *controls]
    comparable = pd.concat(
        [frame[frame[keys].apply(tuple, axis=1).isin(common)] for frame in frames],
        ignore_index=True, sort=False,
    )
    expected = {"E24-A", "E24-B", "E24-C", "E0", "E9", "E14", "E25-A", "E25-B", "E25-C"}
    if set(comparable.experiment) != expected:
        raise ValueError(f"E24 controls incomplete: {sorted(set(comparable.experiment))}")
    if not comparable.groupby(keys, observed=True).experiment.nunique().eq(len(expected)).all():
        raise ValueError("E24 common comparison population is incomplete")
    atomic_parquet(predictions, output / "E24_predictions.parquet")
    atomic_parquet(comparable, output / "E24_common_predictions.parquet")
    calculate_metrics(comparable).to_csv(report_root / "E24_metrics.csv", index=False)
    paired = paired_uncertainty(
        comparable, int(options.bootstrap_samples or (100 if options.smoke_test else config["bootstrap_samples"]))
    )
    paired.to_csv(report_root / "E24_paired_differences.csv", index=False)
    behavior = predictions.assign(
        above_range_error=np.where(
            predictions.actual_above_training_max, predictions.p50 - predictions.actual, np.nan
        )
    ).groupby(["experiment", "target_name", "geography"], observed=True).agg(
        observations=("actual", "size"),
        actual_above_training_max_frequency=("actual_above_training_max", "mean"),
        prediction_above_training_max_frequency=("prediction_above_training_max", "mean"),
        above_range_signed_bias=("above_range_error", "mean"),
    ).reset_index()
    behavior.to_csv(report_root / "E24_training_range_behavior.csv", index=False)
    issuance = pd.DataFrame([
        {"issuance_schedule": "month_start", "evaluated": True,
         "reason": "climate normals are available and predictions are issued at month start"},
        {"issuance_schedule": "day_15_refresh", "evaluated": False,
         "reason": "climate-normal-only isolation has no new day-15 input; result is exactly the month-start forecast"},
    ])
    issuance.to_csv(report_root / "E24_issuance_audit.csv", index=False)
    report = {
        "status": "smoke_passed" if options.smoke_test else "complete",
        "prediction_rows": len(predictions), "common_rows": len(comparable),
        "experiments": sorted(comparable.experiment.unique().tolist()),
        "oracle_rows": int(comparable.oracle_weather.fillna(False).sum()),
        "positive_reference_violations": int((predictions.reference_value <= 0).sum()),
        "reference_after_origin_violations": int((predictions.reference_month > predictions.origin).sum()),
        "future_label_cutoff_violations": int(
            (pd.to_datetime(predictions.label_training_cutoff, utc=True)
             > pd.to_datetime(predictions.operational_forecast_time, utc=True)).sum()
        ),
        "lambda_selection": selections if selections else "preserved from training run",
        "experiments_not_started": ["E26", "E27"],
    }
    (report_root / "E24_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
