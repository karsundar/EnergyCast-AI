#!/usr/bin/env python3
"""Resumable E25 operational-weather Holt-Winters residual correction."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

import duckdb
import numpy as np
import pandas as pd

from forecasting.e25 import (
    assert_e25_leakage_safe, attach_expanding_normals, e25_feature_columns,
    fit_residual_model_at, query_features, residual_available_at, select_lambda,
)
from forecasting.hybrid import historical_oos_residuals
from forecasting.operational_weather import TARGETS, normal_queries, provider_queries

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/phase5d_e25.json"
PHASE4A = ROOT / "config/phase4a.json"


def atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".parquet", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary, index=False, compression="zstd")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    demand = pd.read_parquet(ROOT / "data/processed/ercot_monthly_targets.parquet")
    demand["month"] = pd.to_datetime(demand.month).dt.to_period("M").dt.to_timestamp()
    historical = duckdb.sql(
        f"SELECT * FROM read_parquet('{ROOT}/data/processed/weather/seasonal_compatible_era5/year=*/part.parquet')"
    ).df()
    historical["target_month"] = (
        pd.to_datetime(historical.target_month, utc=True).dt.tz_localize(None).dt.to_period("M").dt.to_timestamp()
    )
    operational = pd.read_parquet(ROOT / "reports/phase5c_stage15/full/operational_weather_features.parquet")
    operational["target_month"] = pd.to_datetime(operational.target_month).dt.to_period("M").dt.to_timestamp()
    seasonal = pd.read_parquet(ROOT / "reports/phase5c/full_v2/weather_skill_predictions.parquet")
    seasonal["target_month"] = pd.to_datetime(seasonal.target_month).dt.to_period("M").dt.to_timestamp()
    baselines = pd.read_parquet(ROOT / "data/processed/phase4c/chronos_test_predictions_comparable.parquet")
    baselines = baselines[baselines.experiment.eq("E0")]
    return demand, historical, operational, seasonal, baselines


def residual_cache(
    demand: pd.DataFrame, historical: pd.DataFrame, target: str, config: dict[str, object],
    rebuild: bool, smoke: bool,
) -> pd.DataFrame:
    destination = ROOT / "data/processed/phase5d" / f"e25_oos_residuals_{target}.parquet"
    if rebuild:
        origins = pd.date_range(config["residual_start_origin"], config["end_origin"], freq="MS")
        raw = historical_oos_residuals(
            demand, target, "holt_winters", origins, int(config["maximum_horizon"]),
            json.loads(PHASE4A.read_text()),
        )
        atomic_parquet(raw, destination)
    elif destination.exists():
        raw = pd.read_parquet(destination)
    else:
        source = ROOT / "data/processed/phase4b" / f"residual_cache_holt_winters_{target}.parquet"
        if not smoke:
            raise RuntimeError(
                f"extended OOS residual cache missing: run with --rebuild-residual-cache ({destination})"
            )
        raw = pd.read_parquet(source)
    raw["origin"] = pd.to_datetime(raw.origin)
    raw["target_month"] = pd.to_datetime(raw.target_month)
    raw["residual_available_at"] = residual_available_at(
        raw.target_month, int(config["residual_publication_lag_days"])
    )
    return attach_expanding_normals(raw, historical, int(config["minimum_normal_years"]))


def make_prediction(
    experiment: str, model: object, columns: list[str], shrinkage: float,
    features: pd.DataFrame, target: str, issuance_time: pd.Timestamp,
    residual_cutoff: pd.Timestamp,
) -> pd.DataFrame:
    correction = model.predict(features[columns])
    prediction = features.baseline_prediction.to_numpy() + shrinkage * correction
    result = features[["origin", "target_month", "horizon", "geography", "actual",
                       "baseline_prediction", "weather_provider", "weather_available_at",
                       "fallback_reason", "training_max"]].copy()
    result["experiment"] = experiment
    result["model"] = "holt_winters_lgbm_operational_weather_residual"
    result["target_name"] = target
    result["p50"] = prediction
    result["predicted_residual"] = correction
    result["shrinkage_lambda"] = shrinkage
    result["residual_training_cutoff"] = residual_cutoff
    result["operational_forecast_time"] = issuance_time
    result["issuance_schedule"] = "month_start" if experiment == "E25-A" else "mid_month_refresh"
    result["oracle_weather"] = False
    result["forecast_status"] = np.where(result.actual.notna(), "complete", "incomplete_horizon")
    result["actual_above_training_max"] = result.actual > result.training_max
    result["prediction_above_training_max"] = result.p50 > result.training_max
    assert_e25_leakage_safe(result)
    return result


def metrics(predictions: pd.DataFrame) -> pd.DataFrame:
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
                "observations": len(group), "wape": float(error.abs().sum()/group.actual.abs().sum()*100),
                "mae": float(error.abs().mean()), "rmse": float(np.sqrt(np.square(error).mean())),
                "signed_bias": float(error.mean()),
            })
    return pd.DataFrame(rows)


def paired_uncertainty(predictions: pd.DataFrame, bootstrap_samples: int) -> pd.DataFrame:
    keys = ["target_name", "origin", "target_month", "horizon", "geography"]
    frame = predictions.copy()
    frame["absolute_error"] = (frame.p50 - frame.actual).abs()
    frame["signed_error"] = frame.p50 - frame.actual
    frame["horizon_band"] = np.where(frame.horizon.le(6), "1-6", "7-12")
    wide = frame.pivot(index=keys, columns="experiment", values=["absolute_error", "signed_error", "actual"])
    bands = frame[keys + ["horizon_band"]].drop_duplicates().set_index(keys)
    rng = np.random.default_rng(42)
    rows = []
    for candidate in ("E25-A", "E25-B", "E25-C"):
        for reference in ("E0", "E9"):
            pair = pd.DataFrame({
                "candidate_absolute_error": wide[("absolute_error", candidate)],
                "reference_absolute_error": wide[("absolute_error", reference)],
                "candidate_signed_error": wide[("signed_error", candidate)],
                "reference_signed_error": wide[("signed_error", reference)],
                "actual": wide[("actual", candidate)],
            }).join(bands).reset_index()
            for scope, scoped in (("all", pair), ("system", pair[pair.geography.eq("ERCOT_SYSTEM")])):
                for (target, band), group in scoped.groupby(["target_name", "horizon_band"], observed=True):
                    n = len(group)
                    indices = rng.integers(0, n, size=(bootstrap_samples, n))
                    absolute_delta = (group.candidate_absolute_error - group.reference_absolute_error).to_numpy()
                    denominator = group.actual.abs().to_numpy()
                    boot_mae = absolute_delta[indices].mean(axis=1)
                    boot_wape = absolute_delta[indices].sum(axis=1) / denominator[indices].sum(axis=1) * 100
                    rows.append({
                        "candidate": candidate, "reference": reference, "scope": scope,
                        "target_name": target, "horizon_band": band, "observations": n,
                        "mean_absolute_error_difference": float(absolute_delta.mean()),
                        "mae_difference_ci_low": float(np.quantile(boot_mae, .025)),
                        "mae_difference_ci_high": float(np.quantile(boot_mae, .975)),
                        "wape_difference": float(absolute_delta.sum()/denominator.sum()*100),
                        "wape_difference_ci_low": float(np.quantile(boot_wape, .025)),
                        "wape_difference_ci_high": float(np.quantile(boot_wape, .975)),
                        "signed_bias_difference": float(
                            (group.candidate_signed_error-group.reference_signed_error).mean()
                        ),
                    })
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--evaluate-only", action="store_true")
    parser.add_argument("--rebuild-residual-cache", action="store_true")
    parser.add_argument("--start-origin")
    parser.add_argument("--end-origin")
    parser.add_argument("--max-horizon", type=int)
    parser.add_argument("--bootstrap-samples", type=int)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    options = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    phase4a = json.loads(PHASE4A.read_text())
    label = "smoke" if options.smoke_test else "full"
    output = ROOT / "data/processed/phase5d/e25" / label
    report_root = ROOT / "reports/phase5d/e25" / label
    partitions = output / "predictions"
    partitions.mkdir(parents=True, exist_ok=True)
    report_root.mkdir(parents=True, exist_ok=True)
    selections = []
    if not options.evaluate_only:
        demand, historical, operational, seasonal, baselines = load_inputs()
        origins = pd.date_range(options.start_origin or config["start_origin"],
                                options.end_origin or config["end_origin"], freq="MS")
        horizon = int(options.max_horizon or config["maximum_horizon"])
        if options.smoke_test:
            origins, horizon = origins[:1], min(horizon, 2)
            query_demand = demand[demand.geography.eq("ERCOT_SYSTEM")]
        else:
            query_demand = demand
        caches = {}
        lambdas = {}
        for target in TARGETS:
            cache = residual_cache(demand, historical, target, config,
                                   options.rebuild_residual_cache, options.smoke_test)
            shrinkage, scores = select_lambda(
                cache, phase4a["lightgbm"], config["lambda_grid"],
                pd.Timestamp(config["development_training_end"]),
                pd.Timestamp(config["development_validation_start"]),
                pd.Timestamp(config["development_validation_end"]),
            )
            caches[target], lambdas[target] = cache, shrinkage
            selections.append({"target_name": target, "selected_lambda": shrinkage,
                               "development_wape": scores})
        for origin in origins:
            for target in TARGETS:
                destinations = {name: partitions / f"{name}_{target}_{origin:%Y%m}.parquet"
                                for name in ("E25-A", "E25-B", "E25-C")}
                if options.resume and all(path.exists() for path in destinations.values()):
                    continue
                target_baselines = baselines[
                    baselines.target_name.eq(target) & baselines.origin.eq(origin)
                    & baselines.horizon.le(horizon)
                    & baselines.geography.isin(query_demand.geography.unique())
                ]
                month_start = (origin + pd.offsets.MonthBegin(1)).tz_localize("UTC")
                midmonth = month_start + pd.Timedelta(days=14, hours=23, minutes=59, seconds=59)
                model, residual_cutoff = fit_residual_model_at(
                    caches[target], month_start, phase4a["lightgbm"]
                )
                columns = e25_feature_columns(caches[target])
                query_sets = {
                    "E25-A": normal_queries(query_demand, operational, origin, target, horizon),
                    "E25-B": provider_queries(query_demand, operational, seasonal, origin, target, horizon, "seas5"),
                    "E25-C": provider_queries(query_demand, operational, seasonal, origin, target, horizon, "ncep"),
                }
                for experiment, weather in query_sets.items():
                    issuance = month_start if experiment == "E25-A" else midmonth
                    available = pd.to_datetime(weather.weather_available_at, utc=True)
                    if (available > issuance).any():
                        raise ValueError(f"{experiment} uses weather released after issuance")
                    feature = query_features(query_demand, weather, target_baselines, target, origin)
                    prediction = make_prediction(
                        experiment, model, columns, lambdas[target], feature, target,
                        issuance, residual_cutoff,
                    )
                    atomic_parquet(prediction, destinations[experiment])
                    print(f"completed {experiment}:{target}:{origin:%Y-%m} rows={len(prediction)}", flush=True)
        (report_root / "lambda_selection.json").write_text(json.dumps(selections, indent=2) + "\n")
    paths = sorted(partitions.glob("E25-*.parquet"))
    if not paths:
        raise RuntimeError("no E25 prediction partitions found")
    predictions = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
    assert_e25_leakage_safe(predictions)
    _, _, _, _, controls = load_inputs()
    e0 = controls[controls.experiment.eq("E0")]
    e9 = pd.read_parquet(ROOT / "data/processed/phase4b/phase4b_predictions_comparable.parquet")
    e9 = e9[e9.experiment.eq("E9")]
    keys = ["target_name", "origin", "target_month", "horizon", "geography"]
    common = set(map(tuple, predictions[keys].to_numpy()))
    for frame in (e0, e9):
        common &= set(map(tuple, frame[keys].to_numpy()))
    comparable = pd.concat([
        predictions[predictions[keys].apply(tuple, axis=1).isin(common)],
        e0[e0[keys].apply(tuple, axis=1).isin(common)],
        e9[e9[keys].apply(tuple, axis=1).isin(common)],
    ], ignore_index=True, sort=False)
    counts = comparable.groupby(keys, observed=True).experiment.nunique()
    if not counts.eq(5).all():
        raise ValueError("E25 common comparison is incomplete")
    atomic_parquet(predictions, output / "E25_predictions.parquet")
    atomic_parquet(comparable, output / "E25_common_predictions.parquet")
    metric = metrics(comparable)
    metric.to_csv(report_root / "E25_metrics.csv", index=False)
    paired = paired_uncertainty(
        comparable, int(options.bootstrap_samples or (100 if options.smoke_test else config["bootstrap_samples"]))
    )
    paired.to_csv(report_root / "E25_paired_differences.csv", index=False)
    behavior = predictions.groupby(["experiment", "target_name", "geography"], observed=True).agg(
        observations=("actual", "size"),
        actual_above_training_max_share=("actual_above_training_max", "mean"),
        prediction_above_training_max_share=("prediction_above_training_max", "mean"),
        bias=("p50", lambda values: float((values - predictions.loc[values.index, "actual"]).mean())),
    ).reset_index()
    behavior.to_csv(report_root / "E25_training_range_behavior.csv", index=False)
    report = {
        "status": "smoke_passed" if options.smoke_test else "complete",
        "prediction_rows": len(predictions), "common_rows": len(comparable),
        "experiments": sorted(comparable.experiment.unique().tolist()),
        "oracle_rows": int(comparable.oracle_weather.fillna(False).sum()), "paired_rows": len(paired),
        "lambda_selection": selections if selections else "preserved from training run",
        "experiments_not_started": ["E24", "E26", "E27"],
    }
    (report_root / "E25_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
