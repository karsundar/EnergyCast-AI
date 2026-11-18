#!/usr/bin/env python3
"""Read-only Phase 5D structural-bias diagnostics and experiment design."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PREDICTIONS = ROOT / "data/processed/phase5c_stage2/gate_b/gate_b_common_predictions.parquet"
TARGETS = ROOT / "data/processed/ercot_monthly_targets.parquet"


def enrich(predictions: pd.DataFrame, demand: pd.DataFrame) -> pd.DataFrame:
    frame = predictions[predictions.experiment.isin(["E18", "E19", "E20"])].copy()
    frame = frame[frame.forecast_status.eq("complete")]
    frame["error"] = frame.p50 - frame.actual
    frame["absolute_error"] = frame.error.abs()
    frame["origin_year"] = frame.origin.dt.year
    frame["target_year"] = frame.target_month.dt.year
    month = frame.target_month.dt.month
    frame["season"] = np.select(
        [month.isin([12, 1, 2]), month.isin([3, 4, 5]), month.isin([6, 7, 8])],
        ["winter", "spring", "summer"], default="fall",
    )
    demand = demand[demand.target_valid].copy()
    demand["month"] = pd.to_datetime(demand.month).dt.to_period("M").dt.to_timestamp()
    indexes = {
        (target, geography): group.set_index("month")[target].sort_index()
        for target in ("total_energy_mwh", "peak_demand_mw")
        for geography, group in demand.groupby("geography", observed=True)
    }
    cache: dict[tuple[str, str, pd.Timestamp], tuple[float, float, float, float]] = {}
    stats = []
    for row in frame.itertuples(index=False):
        key = (row.target_name, row.geography, pd.Timestamp(row.origin))
        if key not in cache:
            history = indexes[(row.target_name, row.geography)].loc[:row.origin].dropna()
            cache[key] = (float(history.max()), float(history.quantile(.25)),
                          float(history.quantile(.75)), float(history.mean()))
        maximum, q25, q75, mean = cache[key]
        series = indexes[(row.target_name, row.geography)]
        prior = series.get(row.target_month - pd.DateOffset(months=12), np.nan)
        growth = float(row.actual / prior - 1) if np.isfinite(prior) and prior else np.nan
        stats.append((maximum, q25, q75, mean, growth))
    stat = pd.DataFrame(stats, columns=["training_max", "training_q25", "training_q75",
                                        "training_mean", "actual_yoy_growth"], index=frame.index)
    frame = pd.concat([frame, stat], axis=1)
    frame["above_training_max"] = frame.actual > frame.training_max
    frame["prediction_above_training_max"] = frame.p50 > frame.training_max
    frame["actual_to_training_max"] = frame.actual / frame.training_max
    frame["demand_level"] = np.select(
        [frame.above_training_max, frame.actual > frame.training_q75,
         frame.actual < frame.training_q25],
        ["above_training_max", "upper_quartile", "lower_quartile"],
        default="middle_50pct",
    )
    frame["growth_regime"] = pd.cut(
        frame.actual_yoy_growth, [-np.inf, 0, .03, .06, np.inf],
        labels=["negative", "0_to_3pct", "3_to_6pct", "over_6pct"],
    ).astype("object").fillna("unavailable")
    frame["horizon_band"] = np.where(frame.horizon.le(6), "1-6", "7-12")
    return frame


def grouped_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    dimensions = {
        "forecast_origin_year": ["origin_year"], "target_year": ["target_year"],
        "forecast_horizon": ["horizon"], "horizon_band": ["horizon_band"],
        "season": ["season"], "ercot_region": ["geography"],
        "demand_level": ["demand_level"], "growth_regime": ["growth_regime"],
    }
    rows = []
    for dimension, columns in dimensions.items():
        groups = ["experiment", "target_name", *columns]
        for keys, group in frame.groupby(groups, observed=True):
            keys = keys if isinstance(keys, tuple) else (keys,)
            rows.append({
                "dimension": dimension, **dict(zip(groups, keys, strict=True)),
                "observations": len(group), "mean_signed_error": float(group.error.mean()),
                "mae": float(group.absolute_error.mean()),
                "rmse": float(np.sqrt(np.mean(np.square(group.error)))),
                "wape": float(group.absolute_error.sum() / group.actual.abs().sum() * 100),
                "underforecast_share": float(group.error.lt(0).mean()),
            })
    return pd.DataFrame(rows)


def distribution_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for keys, group in frame.groupby(["experiment", "target_name", "geography"], observed=True):
        rows.append({
            "experiment": keys[0], "target_name": keys[1], "geography": keys[2],
            "observations": len(group), "actual_mean": float(group.actual.mean()),
            "prediction_mean": float(group.p50.mean()), "actual_p90": float(group.actual.quantile(.9)),
            "prediction_p90": float(group.p50.quantile(.9)), "actual_max": float(group.actual.max()),
            "prediction_max": float(group.p50.max()),
            "actual_above_training_max_share": float(group.above_training_max.mean()),
            "prediction_above_training_max_share": float(group.prediction_above_training_max.mean()),
            "bias_when_actual_above_training_max": float(
                group.loc[group.above_training_max, "error"].mean()
            ),
            "bias_within_training_range": float(
                group.loc[~group.above_training_max, "error"].mean()
            ),
        })
    return pd.DataFrame(rows)


def paired_differences(frame: pd.DataFrame, bootstrap_samples: int) -> pd.DataFrame:
    keys = ["target_name", "origin", "target_month", "horizon", "geography"]
    wide = frame.pivot(index=keys, columns="experiment", values=["absolute_error", "error", "actual"])
    rows = []
    rng = np.random.default_rng(42)
    indexed = frame[keys + ["horizon_band"]].drop_duplicates().set_index(keys)
    for candidate in ("E19", "E20"):
        pair = pd.DataFrame({
            "absolute_error_difference": wide[("absolute_error", candidate)] - wide[("absolute_error", "E18")],
            "signed_error_difference": wide[("error", candidate)] - wide[("error", "E18")],
            "actual": wide[("actual", "E18")],
            "reference_absolute_error": wide[("absolute_error", "E18")],
            "candidate_absolute_error": wide[("absolute_error", candidate)],
        }).join(indexed)
        pair = pair.reset_index()
        scopes = [("all", pair), ("system", pair[pair.geography.eq("ERCOT_SYSTEM")])]
        for scope, scoped in scopes:
            for (target, band), group in scoped.groupby(["target_name", "horizon_band"], observed=True):
                n = len(group)
                indices = rng.integers(0, n, size=(bootstrap_samples, n))
                values = group.absolute_error_difference.to_numpy()
                boot = values[indices].mean(axis=1)
                denominator = np.abs(group.actual.to_numpy())
                reference = group.reference_absolute_error.to_numpy()
                candidate_error = group.candidate_absolute_error.to_numpy()
                boot_wape = (
                    candidate_error[indices].sum(axis=1) - reference[indices].sum(axis=1)
                ) / denominator[indices].sum(axis=1) * 100
                rows.append({
                    "candidate": candidate, "reference": "E18", "scope": scope,
                    "target_name": target, "horizon_band": band, "observations": n,
                    "mean_absolute_error_difference": float(values.mean()),
                    "mae_difference_ci_low": float(np.quantile(boot, .025)),
                    "mae_difference_ci_high": float(np.quantile(boot, .975)),
                    "mean_signed_error_difference": float(group.signed_error_difference.mean()),
                    "wape_difference": float((candidate_error.sum() - reference.sum()) / denominator.sum() * 100),
                    "wape_difference_ci_low": float(np.quantile(boot_wape, .025)),
                    "wape_difference_ci_high": float(np.quantile(boot_wape, .975)),
                })
    return pd.DataFrame(rows)


def issuance_audit(frame: pd.DataFrame) -> pd.DataFrame:
    dynamic = frame[frame.experiment.isin(["E19", "E20"]) & frame.horizon.le(6)].copy()
    dynamic["provider"] = dynamic.experiment.map({"E19": "seas5", "E20": "ncep"})
    dynamic["initialization_month"] = (
        pd.to_datetime(dynamic.weather_initialization, utc=True).dt.tz_localize(None)
        .dt.to_period("M").dt.to_timestamp()
    )
    releases = dynamic[["provider", "initialization_month", "weather_available_at"]].drop_duplicates()
    releases["weather_available_at"] = pd.to_datetime(releases.weather_available_at, utc=True)
    month_start = releases.initialization_month.dt.tz_localize("UTC")
    midmonth = month_start + pd.Timedelta(days=14, hours=23, minutes=59, seconds=59)
    start_rows = releases.assign(
        issuance_schedule="month_start", issuance_time=month_start,
        seasonal_weather_available=releases.weather_available_at.le(month_start),
    )
    mid_rows = releases.assign(
        issuance_schedule="mid_month_refresh", issuance_time=midmonth,
        seasonal_weather_available=releases.weather_available_at.le(midmonth),
    )
    result = pd.concat([start_rows, mid_rows], ignore_index=True)
    result["required_weather_policy"] = np.where(
        result.seasonal_weather_available, "provider_weather", "expanding_climate_normal_fallback"
    )
    result["availability_evidence"] = "scheduled_release_not_file_audit"
    return result


def experiment_design() -> dict[str, object]:
    return {
        "status": "design_only_no_training",
        "selection_policy": "development period only; final test reserved for evaluation",
        "experiments": {
            "E24": {
                "name": "LightGBM growth-target forecasting", "priority": 2,
                "targets": ["log level ratio to origin", "year-over-year growth"],
                "controls": ["E18", "E19", "E20"],
                "gate": "compare transformations on development origins only",
            },
            "E25": {
                "name": "Holt-Winters plus seasonal-weather residual correction", "priority": 1,
                "residuals": "strict rolling-origin out-of-sample Holt-Winters errors only",
                "weather_variants": ["no correction", "climate normal", "SEAS5", "NCEP"],
                "gate": "accept correction only when development bias falls without WAPE increase",
            },
            "E26": {
                "name": "Chronos-2 plus seasonal future covariates", "priority": 4,
                "compatibility": "verified installed Chronos-2 model declares future-covariate support",
                "environment": ".venv-chronos2; preserve ROCm stack; smoke required before any run",
            },
            "E27": {
                "name": "SARIMAX plus operational seasonal weather", "priority": 3,
                "controls": ["weather-independent SARIMA", "normal weather", "SEAS5", "NCEP"],
                "gate": "same rolling origins and scheduled-availability policy",
            },
        },
        "issuance": {
            "month_start": "provider release unavailable; use expanding normals",
            "mid_month_refresh": "use provider weather only when scheduled available_at <= issuance time",
        },
        "prohibited": ["future ERA5 predictors", "in-sample-only residual targets",
                       "final-test model selection", "daily features from monthly weather"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--bootstrap-samples", type=int, default=1000)
    args = parser.parse_args()
    output = ROOT / "reports/phase5d" / ("smoke" if args.smoke_test else "diagnostics")
    output.mkdir(parents=True, exist_ok=True)
    predictions = pd.read_parquet(PREDICTIONS)
    demand = pd.read_parquet(TARGETS)
    if args.smoke_test:
        predictions = predictions[
            predictions.geography.eq("ERCOT_SYSTEM") & predictions.origin.eq(predictions.origin.min())
        ]
    frame = enrich(predictions, demand)
    metrics = grouped_metrics(frame)
    distributions = distribution_summary(frame)
    paired = paired_differences(frame, min(args.bootstrap_samples, 100 if args.smoke_test else args.bootstrap_samples))
    issuance = issuance_audit(frame)
    frame.to_parquet(output / "enriched_predictions.parquet", index=False, compression="zstd")
    metrics.to_parquet(output / "bias_breakdowns.parquet", index=False, compression="zstd")
    distributions.to_parquet(output / "distribution_shift.parquet", index=False, compression="zstd")
    paired.to_parquet(output / "paired_error_differences.parquet", index=False, compression="zstd")
    issuance.to_parquet(output / "issuance_availability_audit.parquet", index=False, compression="zstd")
    design = experiment_design()
    (output / "experiment_design.json").write_text(json.dumps(design, indent=2) + "\n")
    system = frame[frame.geography.eq("ERCOT_SYSTEM")]
    range_summary = system.groupby(["experiment", "target_name"], observed=True).agg(
        rows=("error", "size"), bias=("error", "mean"), mae=("absolute_error", "mean"),
        actual_above_training_max_share=("above_training_max", "mean"),
        prediction_above_training_max_share=("prediction_above_training_max", "mean"),
    ).reset_index()
    report = {
        "status": "smoke_passed" if args.smoke_test else "complete",
        "stage": "Phase 5D structural-bias diagnostics only",
        "prediction_rows": len(frame), "bias_breakdown_rows": len(metrics),
        "paired_rows": len(paired), "issuance_rows": len(issuance),
        "explicit_prediction_clipping": False, "target_normalization": False,
        "forecast_strategy": "direct multi-horizon, not recursive",
        "chronos2_future_covariates_supported": True,
        "experiments_started": [], "experiments_designed": ["E24", "E25", "E26", "E27"],
        "system_training_range_summary": range_summary.to_dict("records"),
    }
    (output / "diagnostic_summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
