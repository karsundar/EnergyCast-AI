#!/usr/bin/env python3
"""Fit E14 ensemble weights only on validation predictions, then apply to test forecasts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
PHASE4B = PROCESSED / "phase4b"
PHASE4C = PROCESSED / "phase4c"
REPORTS = ROOT / "reports" / "phase4b"
KEYS = ["target_name", "origin", "target_month", "horizon", "geography"]


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validation-predictions", type=Path, default=PHASE4C / "validation_predictions.parquet")
    parser.add_argument("--test-predictions", type=Path,
                        default=PROCESSED / "phase4c" / "chronos_test_predictions_comparable.parquet")
    parser.add_argument("--candidates", default="E0,E8,E9,E11,E13")
    parser.add_argument("--mode", choices=("non-oracle", "oracle-mixed"), default="non-oracle")
    parser.add_argument("--smoke-test", action="store_true")
    return parser.parse_args()


def read_validation(path: Path) -> pd.DataFrame:
    frames = [pd.read_parquet(path)]
    partition_dir = path.parent / "validation"
    frames.extend(pd.read_parquet(part) for part in sorted(partition_dir.glob("*.parquet")))
    return pd.concat(frames, ignore_index=True)


def learn_weights(frame: pd.DataFrame, candidates: list[str]) -> dict[str, float]:
    pivot = frame[frame["experiment"].isin(candidates)].pivot(index=KEYS, columns="experiment", values="p50").dropna()
    actual = frame.drop_duplicates(KEYS).set_index(KEYS)["actual"].reindex(pivot.index).to_numpy()
    available = list(pivot.columns)
    values = pivot.to_numpy()
    result = minimize(
        lambda weights: np.abs(values @ weights - actual).sum() / np.abs(actual).sum(),
        x0=np.full(len(available), 1 / len(available)), method="SLSQP",
        bounds=[(0, 1)] * len(available), constraints={"type": "eq", "fun": lambda weights: weights.sum() - 1},
    )
    if not result.success:
        raise RuntimeError(f"ensemble optimization failed: {result.message}")
    return dict(zip(available, map(float, result.x), strict=True))


def main() -> int:
    options = arguments()
    if not options.validation_predictions.exists():
        raise FileNotFoundError(
            f"{options.validation_predictions} is required; E14 weights must not be learned from test predictions"
        )
    validation = read_validation(options.validation_predictions)
    test = pd.read_parquet(options.test_predictions)
    candidates = options.candidates.split(",")
    oracle = {"E1", "E3", "E5", "E6", "E7", "E10", "E12"}
    if options.mode == "non-oracle" and set(candidates) & oracle:
        raise ValueError("non-oracle E14 candidates include an oracle experiment")
    outputs = []
    learned = {}
    for target, validation_target in validation.groupby("target_name", observed=True):
        weights = learn_weights(validation_target, candidates)
        learned[target] = weights
        test_target = test[(test["target_name"] == target) & test["experiment"].isin(weights)]
        pivot = test_target.pivot(index=KEYS, columns="experiment", values="p50").dropna(subset=list(weights))
        if options.smoke_test:
            pivot = pivot.head(24)
        forecast = sum(pivot[experiment] * weight for experiment, weight in weights.items())
        actual = test_target.drop_duplicates(KEYS).set_index(KEYS)["actual"].reindex(pivot.index)
        output = pivot.index.to_frame(index=False)
        output["p50"] = forecast.to_numpy(); output["actual"] = actual.to_numpy()
        output["experiment"] = "E14"; output["model"] = "validation_weighted_ensemble"
        output["frequency"] = "monthly"; output["forecast_path"] = "direct_monthly"
        output["oracle_weather"] = any(name in oracle and weight > 0 for name, weight in weights.items())
        output["forecast_status"] = "complete"
        outputs.append(output)
    combined = pd.concat(outputs, ignore_index=True)
    PHASE4B.mkdir(parents=True, exist_ok=True); REPORTS.mkdir(parents=True, exist_ok=True)
    suffix = "_smoke" if options.smoke_test else ""
    combined.to_parquet(PHASE4B / f"E14_predictions_{options.mode}{suffix}.parquet",
                        index=False, compression="zstd")
    (REPORTS / f"E14_weights_{options.mode}.json").write_text(
        json.dumps({"selection_period": "validation_only", "mode": options.mode, "weights": learned}, indent=2) + "\n")
    metrics = []
    for scope, scope_frame in (("system", combined[combined["geography"] == "ERCOT_SYSTEM"]),
                               ("all_regions", combined)):
        for target, target_frame in scope_frame.groupby("target_name", observed=True):
            error = target_frame["p50"] - target_frame["actual"]
            metrics.append({"scope": scope, "target_name": target,
                            "observations": len(target_frame),
                            "wape": float(error.abs().sum() / target_frame["actual"].abs().sum() * 100),
                            "mae": float(error.abs().mean()), "bias": float(error.mean())})
    (REPORTS / f"E14_metrics_{options.mode}{suffix}.json").write_text(
        json.dumps({"mode": options.mode, "metrics": metrics}, indent=2) + "\n")
    print(json.dumps(learned, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
