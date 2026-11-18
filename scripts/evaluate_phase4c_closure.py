#!/usr/bin/env python3
"""Evaluate Phase 4C bias and calibration separately for system and regions."""

from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
REPORTS = ROOT / "reports" / "phase4c"


def metrics(frame: pd.DataFrame, experiment: str, scope: str) -> list[dict[str, object]]:
    records = []
    for target, group in frame.groupby("target_name", observed=True):
        error = group["p50"] - group["actual"]
        losses = []
        for quantile, column in ((.1, "p10"), (.5, "p50"), (.9, "p90")):
            delta = group["actual"] - group[column]
            losses.append(np.maximum(quantile * delta, (quantile - 1) * delta).mean())
        records.append({
            "experiment": experiment, "scope": scope, "target_name": target,
            "observations": len(group), "wape": float(error.abs().sum() / group["actual"].abs().sum() * 100),
            "bias": float(error.mean()), "normalized_bias_pct": float(error.sum() / group["actual"].abs().sum() * 100),
            "coverage_p10_p90": float(group["actual"].between(group["p10"], group["p90"]).mean()),
            "mean_interval_width": float((group["p90"] - group["p10"]).mean()),
            "mean_pinball_loss": float(np.mean(losses)),
        })
    return records


def main() -> int:
    records = []
    for experiment in ("E15", "E16"):
        frame = pd.read_parquet(PROCESSED / "phase4c" / f"{experiment}_predictions.parquet")
        records.extend(metrics(frame[frame["geography"] == "ERCOT_SYSTEM"], experiment, "system"))
        records.extend(metrics(frame[frame["geography"] != "ERCOT_SYSTEM"], experiment, "regional"))
    result = pd.DataFrame(records)
    REPORTS.mkdir(parents=True, exist_ok=True)
    result.to_csv(REPORTS / "phase4c_closure_metrics.csv", index=False)
    status = {
        "E13": "smoke_and_validation_complete_full_finetuning_deferred",
        "E15": "complete",
        "E16": "complete_system_target_met_regional_undercoverage_documented",
        "E17": "smoke_complete_full_backtest_pending",
        "metrics": records,
    }
    (REPORTS / "phase4c_closure.json").write_text(json.dumps(status, indent=2) + "\n")
    print(json.dumps(status, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
