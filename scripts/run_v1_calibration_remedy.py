#!/usr/bin/env python3
"""Blocked development evaluation for prospective V1 calibration remedies."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from forecasting.v1_calibration_remedy import (
    add_horizon_band, apply_remedy, fit_remedy, label_available_at,
    score_intervals, select_methods,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/v1_calibration_remedy.json"
DEVELOPMENT = ROOT / "data/processed/v1/full/v1_development_predictions.parquet"
REPORTS = ROOT / "reports/v1/calibration_remedy"


def load_development(config: dict[str, object]) -> pd.DataFrame:
    frame = pd.read_parquet(DEVELOPMENT)
    for column in ("origin", "target_month"):
        frame[column] = pd.to_datetime(frame[column])
    if frame.target_month.max() > pd.Timestamp("2020-12-01"):
        raise ValueError("calibration remedy input contains final-test observations")
    selected = pd.concat([
        frame[frame.target_name.eq(target) & frame.experiment.eq(experiment)]
        for target, experiment in config["selected_models"].items()
    ], ignore_index=True, sort=False)
    selected["label_available_at"] = label_available_at(selected.target_month)
    selected = add_horizon_band(selected, config["horizon_bands"])
    demand = pd.read_parquet(ROOT / "data/processed/ercot_monthly_targets.parquet")
    demand["month"] = pd.to_datetime(demand.month).dt.to_period("M").dt.to_timestamp()
    lookup = demand.set_index(["geography", "month"])
    growth = []
    for row in selected.itertuples(index=False):
        prior = lookup.loc[(row.geography, row.target_month - pd.DateOffset(months=12)), row.target_name]
        growth.append(float(row.actual / prior - 1) if prior else np.nan)
    selected["actual_yoy_growth"] = growth
    return selected


def blocked_evaluation(
    development: pd.DataFrame, config: dict[str, object], smoke: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    metrics = []
    predictions = []
    folds = config["blocked_folds"][:1] if smoke else config["blocked_folds"]
    for fold in folds:
        start, end = pd.Timestamp(fold["start"]), pd.Timestamp(fold["end"])
        eligible = development[
            development.label_available_at.le(start)
            & development.origin.lt(start)
        ].copy()
        evaluation = development[development.origin.between(start, end)].copy()
        if smoke:
            origins = sorted(evaluation.origin.unique())[:2]
            evaluation = evaluation[evaluation.origin.isin(origins)]
        if eligible.empty or evaluation.empty:
            raise ValueError(f"blocked fold {fold['name']} lacks fit/evaluation rows")
        thresholds = eligible.groupby("target_name", observed=True).actual_yoy_growth.quantile(.75)
        evaluation["growth_regime"] = np.where(
            evaluation.actual_yoy_growth >= evaluation.target_name.map(thresholds),
            "high_growth", "normal_growth",
        )
        if (eligible.label_available_at > start).any():
            raise AssertionError("blocked calibration uses residual unavailable at fold issuance")
        for method in config["methods"]:
            fitted = fit_remedy(
                eligible, method, float(config["target_coverage"]),
                int(config["minimum_band_samples"]),
            )
            calibrated = apply_remedy(evaluation, fitted)
            calibrated["blocked_fold"] = fold["name"]
            calibrated["calibration_training_cutoff"] = eligible.label_available_at.max()
            calibrated["evaluation_start"] = start
            if (calibrated.calibration_training_cutoff > calibrated.evaluation_start).any():
                raise AssertionError("calibration cutoff crosses blocked evaluation origin")
            predictions.append(calibrated)
            metrics.append(score_intervals(calibrated, fold["name"]))
    return pd.concat(predictions, ignore_index=True), pd.concat(metrics, ignore_index=True)


def frozen_tables(
    development: pd.DataFrame, selected: pd.DataFrame, config: dict[str, object], destination: Path,
) -> None:
    tables = {"band": [], "fallback": [], "scales": []}
    for row in selected.itertuples(index=False):
        scope_rows = development[
            development.target_name.eq(row.target_name)
            & (development.geography.eq("ERCOT_SYSTEM") if row.scope == "system"
               else development.geography.ne("ERCOT_SYSTEM"))
        ]
        fitted = fit_remedy(
            scope_rows, row.calibration_method, float(config["target_coverage"]),
            int(config["minimum_band_samples"]),
        )
        for name, table in (
            ("band", fitted.by_band), ("fallback", fitted.fallback), ("scales", fitted.scales),
        ):
            item = table.copy()
            item["selected_scope"] = row.scope
            item["calibration_method"] = row.calibration_method
            tables[name].append(item)
    for name, frames in tables.items():
        pd.concat(frames, ignore_index=True).to_csv(destination / f"frozen_{name}_calibration.csv", index=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true")
    options = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    label = "smoke" if options.smoke_test else "full"
    destination = REPORTS / label
    destination.mkdir(parents=True, exist_ok=True)
    development = load_development(config)
    predictions, metrics = blocked_evaluation(development, config, options.smoke_test)
    selected, comparison = select_methods(
        metrics, float(config["target_coverage"]), float(config["coverage_penalty_weight"])
    )
    frozen_tables(development, selected, config, destination)
    predictions.to_parquet(
        destination / "blocked_predictions.parquet", index=False, compression="zstd"
    )
    metrics.to_csv(destination / "blocked_metrics.csv", index=False)
    comparison.to_csv(destination / "method_comparison.csv", index=False)
    selected.to_csv(destination / "selected_methods.csv", index=False)
    report = {
        "status": "smoke_passed" if options.smoke_test else "development_complete",
        "input_target_end": str(development.target_month.max().date()),
        "final_test_rows_read": 0,
        "blocked_prediction_rows": len(predictions),
        "blocked_folds": sorted(predictions.blocked_fold.unique().tolist()),
        "future_calibration_cutoff_violations": int((
            pd.to_datetime(predictions.calibration_training_cutoff)
            > pd.to_datetime(predictions.evaluation_start)
        ).sum()),
        "selected_methods": selected.to_dict("records"),
        "target_coverage": config["target_coverage"],
        "independent_holdout_available": config["independent_holdout_available"],
        "confirmation_status": "awaiting_prospective_or_independently_held_out_confirmation",
        "deterministic_forecasts_enabled": True,
        "probabilistic_intervals_enabled": False,
        "final_test_results_preserved_unchanged": True,
        "experiments_not_started": ["E26", "E27"],
    }
    (destination / "calibration_remedy_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
