#!/usr/bin/env python3
"""Run validation-only E15 bias correction or E16 scale-aware calibration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import pandas as pd

from forecasting.calibration import (
    apply_calibration, apply_interval_inflation, fit_calibration,
    fit_interval_inflation, select_method,
)

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
PHASE4C = PROCESSED / "phase4c"
REPORTS = ROOT / "reports" / "phase4c"


def read_validation(path: Path) -> pd.DataFrame:
    frames = [pd.read_parquet(path)]
    frames.extend(pd.read_parquet(part) for part in sorted((path.parent / "validation").glob("*.parquet")))
    return pd.concat(frames, ignore_index=True)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", choices=("E15", "E16"), required=True)
    parser.add_argument("--validation-predictions", type=Path, default=PHASE4C / "validation_predictions.parquet")
    parser.add_argument("--test-predictions", type=Path, default=PROCESSED / "phase4b" / "phase4b_predictions_comparable.parquet")
    parser.add_argument("--energy-base", default="E10")
    parser.add_argument("--peak-base", default="E1")
    parser.add_argument("--min-group-samples", type=int, default=12)
    parser.add_argument("--smoke-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    options = arguments()
    validation = read_validation(options.validation_predictions)
    test = pd.read_parquet(options.test_predictions)
    outputs, report = [], {}
    for target, base in (("total_energy_mwh", options.energy_base), ("peak_demand_mw", options.peak_base)):
        development = validation[(validation["target_name"] == target) & (validation["experiment"] == base)].copy()
        evaluation = test[(test["target_name"] == target) & (test["experiment"] == base)].copy()
        if options.smoke_test:
            evaluation = evaluation.head(24)
        objective = "wape" if options.experiment == "E15" else "pinball"
        selected, scores = select_method(development, options.min_group_samples, objective)
        fitted = fit_calibration(development, selected, options.min_group_samples)
        result = apply_calibration(evaluation, fitted, correct_bias=True)
        inflation_summary = None
        if options.experiment == "E16":
            months = pd.to_datetime(development["target_month"])
            inflation_fit = development[months < pd.Timestamp("2020-01-01")]
            inflation_holdout = development[
                months.between(pd.Timestamp("2020-01-01"), pd.Timestamp("2020-12-01"))
            ]
            holdout_calibration = fit_calibration(inflation_fit, selected, options.min_group_samples)
            calibrated_holdout = apply_calibration(inflation_holdout, holdout_calibration, correct_bias=True)
            factors = fit_interval_inflation(calibrated_holdout, target_coverage=.8)
            result = apply_interval_inflation(result, factors)
            validation_inflated = apply_interval_inflation(calibrated_holdout, factors)
            inflation_summary = {
                "minimum": float(factors["interval_inflation"].min()),
                "median": float(factors["interval_inflation"].median()),
                "maximum": float(factors["interval_inflation"].max()),
                "holdout_coverage": float(validation_inflated["actual"].between(
                    validation_inflated["p10"], validation_inflated["p90"]
                ).mean()),
            }
        result["experiment"] = options.experiment
        result["model"] = ("validation_bias_correction" if options.experiment == "E15"
                           else "scale_aware_calibration")
        result["calibration_method"] = selected
        outputs.append(result)
        report[target] = {"base": base, "selection_objective": objective,
                          "selected_method": selected, "development_scores": scores,
                          "group_calibrations": len(fitted.group), "target_coverage": .8,
                          "interval_inflation": inflation_summary}
    combined = pd.concat(outputs, ignore_index=True)
    PHASE4C.mkdir(parents=True, exist_ok=True); REPORTS.mkdir(parents=True, exist_ok=True)
    suffix = "_smoke" if options.smoke_test else ""
    combined.to_parquet(PHASE4C / f"{options.experiment}_predictions{suffix}.parquet", index=False, compression="zstd")
    (REPORTS / f"{options.experiment}_calibration{suffix}.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
