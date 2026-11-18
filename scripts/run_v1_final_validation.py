#!/usr/bin/env python3
"""EnergyCast V1 development selection and scale-aware interval calibration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

import duckdb
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from forecasting.e24 import e24_feature_columns, reconstruct
from forecasting.v1_calibration import (
    KEYS, apply_scale_aware_intervals, assert_operational_safety,
    fit_scale_aware_intervals, interval_metrics,
)
from run_phase5d_e24 import training_samples

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/v1_final_validation.json"
E24_CONFIG = ROOT / "config/phase5d_e24.json"
OUTPUT = ROOT / "data/processed/v1"
REPORTS = ROOT / "reports/v1"


def atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".parquet", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary, index=False, compression="zstd")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validation_predictions() -> pd.DataFrame:
    path = ROOT / "data/processed/phase4c/validation_predictions.parquet"
    frames = [pd.read_parquet(path)]
    frames.extend(pd.read_parquet(item) for item in sorted((path.parent / "validation").glob("*.parquet")))
    frame = pd.concat(frames, ignore_index=True)
    for column in ("origin", "target_month"):
        frame[column] = pd.to_datetime(frame[column])
    return frame


def e14_development(validation: pd.DataFrame) -> pd.DataFrame:
    weights = json.loads((ROOT / "reports/phase4b/E14_weights_non-oracle.json").read_text())["weights"]
    outputs = []
    for target, target_weights in weights.items():
        candidates = list(target_weights)
        subset = validation[
            validation.target_name.eq(target) & validation.experiment.isin(candidates)
        ]
        pivot = subset.pivot(index=KEYS, columns="experiment", values="p50").dropna(subset=candidates)
        actual = subset.drop_duplicates(KEYS).set_index(KEYS).actual.reindex(pivot.index)
        output = pivot.index.to_frame(index=False)
        output["p50"] = sum(pivot[name] * weight for name, weight in target_weights.items()).to_numpy()
        output["actual"] = actual.to_numpy()
        output["experiment"] = "E14"
        output["oracle_weather"] = False
        outputs.append(output)
    return pd.concat(outputs, ignore_index=True)


def e24b_development() -> pd.DataFrame:
    config = json.loads(E24_CONFIG.read_text())
    lambdas = {
        row["target_name"]: float(row["selected_lambda"])
        for row in json.loads((ROOT / "reports/phase5d/e24/full/lambda_selection.json").read_text())
        if row["reference_type"] == "holt_winters_oos"
    }
    demand = pd.read_parquet(ROOT / "data/processed/ercot_monthly_targets.parquet")
    demand["month"] = pd.to_datetime(demand.month).dt.to_period("M").dt.to_timestamp()
    historical = duckdb.sql(
        f"SELECT * FROM read_parquet('{ROOT}/data/processed/weather/seasonal_compatible_era5/year=*/part.parquet')"
    ).df()
    historical["target_month"] = (
        pd.to_datetime(historical.target_month, utc=True).dt.tz_localize(None)
        .dt.to_period("M").dt.to_timestamp()
    )
    outputs = []
    for target in ("total_energy_mwh", "peak_demand_mw"):
        samples = training_samples(demand, historical, target, config, "holt_winters_oos")
        columns = e24_feature_columns(samples)
        train = samples[samples.target_month.le(config["development_training_end"])].dropna(
            subset=columns + ["log_ratio"]
        )
        development = samples[
            samples.target_month.between("2018-01-01", "2020-12-01")
        ].dropna(subset=columns + ["log_ratio"])
        model = LGBMRegressor(objective="regression_l1", verbosity=-1, **config["lightgbm"])
        model.fit(train[columns], train.log_ratio)
        output = development[[column for column in KEYS if column != "target_name"] + ["actual"]].copy()
        output["target_name"] = target
        output = output[KEYS + ["actual"]]
        output["p50"] = reconstruct(
            development.reference_value.to_numpy(), model.predict(development[columns]), lambdas[target]
        )
        output["experiment"] = "E24-B"
        output["oracle_weather"] = False
        outputs.append(output)
    return pd.concat(outputs, ignore_index=True)


def select_models(development: pd.DataFrame, config: dict[str, object]) -> tuple[dict[str, str], pd.DataFrame]:
    selection = development[
        development.target_month.between(config["selection_start"], config["selection_end"])
        & development.geography.eq("ERCOT_SYSTEM")
    ].copy()
    rows = []
    chosen = {}
    for target, candidates in config["eligible_candidates"].items():
        target_frame = selection[
            selection.target_name.eq(target) & selection.experiment.isin(candidates)
        ]
        common = set(map(tuple, target_frame[target_frame.experiment.eq(candidates[0])][KEYS].to_numpy()))
        for candidate in candidates[1:]:
            common &= set(map(tuple, target_frame[target_frame.experiment.eq(candidate)][KEYS].to_numpy()))
        target_frame = target_frame[target_frame[KEYS].apply(tuple, axis=1).isin(common)]
        for experiment, group in target_frame.groupby("experiment", observed=True):
            error = group.p50 - group.actual
            rows.append({
                "target_name": target, "experiment": experiment, "observations": len(group),
                "wape": float(error.abs().sum() / group.actual.abs().sum() * 100),
                "mae": float(error.abs().mean()), "rmse": float(np.sqrt(np.square(error).mean())),
                "signed_bias": float(error.mean()),
            })
        metrics = pd.DataFrame([row for row in rows if row["target_name"] == target])
        chosen[target] = str(metrics.sort_values(["wape", "mae"]).iloc[0].experiment)
    return chosen, pd.DataFrame(rows)


def test_candidates() -> pd.DataFrame:
    e0 = pd.read_parquet(ROOT / "data/processed/phase4c/chronos_test_predictions_comparable.parquet")
    e0 = e0[e0.experiment.eq("E0")]
    e14 = pd.read_parquet(ROOT / "data/processed/phase4b/E14_predictions_non-oracle.parquet")
    e24 = pd.read_parquet(ROOT / "data/processed/phase5d/e24/full/E24_predictions.parquet")
    e24 = e24[e24.experiment.eq("E24-B")]
    frame = pd.concat([e0, e14, e24], ignore_index=True, sort=False)
    for column in ("origin", "target_month"):
        frame[column] = pd.to_datetime(frame[column])
    inferred_status = pd.Series(
        np.where(frame.actual.notna(), "complete", "incomplete_horizon"), index=frame.index
    )
    frame["forecast_status"] = frame.forecast_status.fillna(inferred_status)
    return frame


def comparison_uncertainty(frame: pd.DataFrame, bootstrap_samples: int) -> pd.DataFrame:
    system = frame[frame.geography.eq("ERCOT_SYSTEM")].copy()
    system["absolute_error"] = (system.p50 - system.actual).abs()
    wide = system.pivot(index=KEYS, columns="experiment", values=["absolute_error", "actual"])
    rng = np.random.default_rng(42)
    rows = []
    for target in system.target_name.unique():
        for candidate in ("E14", "E24-B"):
            for reference in ("E0", "E14", "E24-B"):
                if candidate == reference:
                    continue
                subset = wide.loc[wide.index.get_level_values("target_name") == target]
                delta = (subset[("absolute_error", candidate)] - subset[("absolute_error", reference)]).to_numpy()
                actual = subset[("actual", candidate)].abs().to_numpy()
                indices = rng.integers(0, len(delta), size=(bootstrap_samples, len(delta)))
                boot = delta[indices].sum(axis=1) / actual[indices].sum(axis=1) * 100
                rows.append({
                    "target_name": target, "candidate": candidate, "reference": reference,
                    "observations": len(delta), "wape_difference": float(delta.sum() / actual.sum() * 100),
                    "ci_low": float(np.quantile(boot, .025)), "ci_high": float(np.quantile(boot, .975)),
                })
    return pd.DataFrame(rows)


def add_training_range(frame: pd.DataFrame) -> pd.DataFrame:
    demand = pd.read_parquet(ROOT / "data/processed/ercot_monthly_targets.parquet")
    demand["month"] = pd.to_datetime(demand.month).dt.to_period("M").dt.to_timestamp()
    maxima = []
    for row in frame.itertuples(index=False):
        values = demand[
            demand.geography.eq(row.geography) & demand.month.le(row.origin)
        ][row.target_name]
        maxima.append(float(values.max()))
    result = frame.copy()
    result["training_max"] = maxima
    result["actual_above_training_max"] = result.actual > result.training_max
    result["prediction_above_training_max"] = result.p50 > result.training_max
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--bootstrap-samples", type=int)
    options = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    label = "smoke" if options.smoke_test else "full"
    output = OUTPUT / label
    reports = REPORTS / label
    output.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)

    validation = validation_predictions()
    e0_development = validation[validation.experiment.eq("E0")].copy()
    development = pd.concat(
        [e0_development, e14_development(validation), e24b_development()],
        ignore_index=True, sort=False,
    )
    selected, selection_metrics = select_models(development, config)
    calibration_rows = []
    final_rows = []
    candidates = test_candidates()
    if options.smoke_test:
        first_origin = candidates.origin.min()
        candidates = candidates[
            candidates.origin.eq(first_origin) & candidates.geography.eq("ERCOT_SYSTEM")
        ]
    for target, experiment in selected.items():
        calibration_rows.append(development[
            development.target_name.eq(target) & development.experiment.eq(experiment)
            & development.target_month.between(config["calibration_start"], config["calibration_end"])
        ])
        final_rows.append(candidates[
            candidates.target_name.eq(target) & candidates.experiment.eq(experiment)
        ])
    calibration = pd.concat(calibration_rows, ignore_index=True)
    evaluation = pd.concat(final_rows, ignore_index=True)
    fitted = fit_scale_aware_intervals(
        calibration, float(config["target_coverage"]), int(config["minimum_horizon_samples"])
    )
    calibrated = apply_scale_aware_intervals(evaluation, fitted)
    calibrated = add_training_range(calibrated)
    assert_operational_safety(calibrated, pd.Timestamp(config["final_test_start"]))
    calibrated["above_range_error"] = np.where(
        calibrated.actual_above_training_max, calibrated.p50 - calibrated.actual, np.nan
    )
    metrics = interval_metrics(calibrated)
    behavior = calibrated.groupby(["target_name", "geography"], observed=True).agg(
        observations=("actual", "size"),
        actual_above_training_max_frequency=("actual_above_training_max", "mean"),
        prediction_above_training_max_frequency=("prediction_above_training_max", "mean"),
        above_range_signed_bias=("above_range_error", "mean"),
    ).reset_index()
    uncertainty = comparison_uncertainty(
        test_candidates(), int(options.bootstrap_samples or (100 if options.smoke_test else config["bootstrap_samples"]))
    )
    latest = calibrated[calibrated.origin.eq(calibrated.origin.max())]
    twelve_month_valid = (
        latest.groupby(["target_name", "geography"], observed=True).horizon
        .apply(lambda values: set(values) == set(range(1, 13))).all()
    )
    issuance = pd.DataFrame([
        {"schedule": "month_start", "eligible": True,
         "weather_policy": "E14 weather-independent; E24-B expanding climate normals"},
        {"schedule": "day_15_refresh", "eligible": True,
         "weather_policy": "No new input for frozen V1 candidates; forecast is invariant to schedule"},
    ])
    availability = pd.DataFrame([
        {"model": "E14", "future_observed_weather": False, "provider_requirement": "none",
         "fallback": "not applicable"},
        {"model": "E24-B", "future_observed_weather": False,
         "provider_requirement": "expanding climate normals available at origin",
         "fallback": "same expanding climate-normal policy at horizons 1-12"},
    ])

    atomic_parquet(calibrated, output / "v1_calibrated_predictions.parquet")
    atomic_parquet(development, output / "v1_development_predictions.parquet")
    atomic_parquet(latest, output / "v1_latest_12_month_validation.parquet")
    selection_metrics.to_csv(reports / "model_selection_metrics.csv", index=False)
    metrics.to_csv(reports / "probabilistic_metrics.csv", index=False)
    behavior.to_csv(reports / "training_range_behavior.csv", index=False)
    uncertainty.to_csv(reports / "model_comparison_uncertainty.csv", index=False)
    issuance.to_csv(reports / "issuance_audit.csv", index=False)
    availability.to_csv(reports / "availability_audit.csv", index=False)
    fitted.by_horizon.to_csv(reports / "horizon_calibration.csv", index=False)
    fitted.by_geography.to_csv(reports / "geography_fallback_calibration.csv", index=False)
    report = {
        "status": "smoke_passed" if options.smoke_test else "complete",
        "selected_models": selected,
        "selection_period": [config["selection_start"], config["selection_end"]],
        "calibration_period": [config["calibration_start"], config["calibration_end"]],
        "final_test_start": config["final_test_start"],
        "calibrated_prediction_rows": len(calibrated),
        "system_rows": int(calibrated.geography.eq("ERCOT_SYSTEM").sum()),
        "regional_rows": int(calibrated.geography.ne("ERCOT_SYSTEM").sum()),
        "twelve_month_generation_valid": bool(twelve_month_valid),
        "oracle_rows": int(calibrated.oracle_weather.fillna(False).sum()),
        "quantile_crossing_rows": int(((calibrated.p10 > calibrated.p50) | (calibrated.p50 > calibrated.p90)).sum()),
        "weather_available_after_issuance_rows": int((
            pd.to_datetime(calibrated.weather_available_at, utc=True)
            > pd.to_datetime(calibrated.operational_forecast_time, utc=True)
        ).fillna(False).sum()),
        "calibration_uses_final_test": False,
        "calibration_method": config["calibration_method"],
        "ineligible_challengers": config["ineligible_challengers"],
        "experiments_not_started": ["E26", "E27"],
    }
    (reports / "v1_validation_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
