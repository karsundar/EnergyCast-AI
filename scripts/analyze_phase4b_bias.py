#!/usr/bin/env python3
"""Summarize systematic underforecasting without fitting models."""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "data" / "processed" / "phase4b" / "phase4b_predictions_comparable.parquet"
output = ROOT / "reports" / "phase4b"
frame = pd.read_parquet(source)
frame = frame[frame["forecast_status"] == "complete"].copy()
frame["error"] = frame["p50"] - frame["actual"]
frame["absolute_error"] = frame["error"].abs()
frame["year"] = pd.to_datetime(frame["target_month"]).dt.year
frame["season"] = pd.to_datetime(frame["target_month"]).dt.month.map(
    {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
     6: "summer", 7: "summer", 8: "summer", 9: "fall", 10: "fall", 11: "fall"}
)
records = []
for breakdown, columns in {
    "year": ["experiment", "target_name", "year"],
    "region": ["experiment", "target_name", "geography"],
    "horizon": ["experiment", "target_name", "horizon"],
    "season": ["experiment", "target_name", "season"],
}.items():
    for keys, group in frame.groupby(columns, observed=True):
        keys = keys if isinstance(keys, tuple) else (keys,)
        records.append({
            "breakdown": breakdown, **dict(zip(columns, keys, strict=True)), "observations": len(group),
            "bias": group["error"].mean(), "mean_actual": group["actual"].mean(),
            "normalized_bias_pct": group["error"].sum() / group["actual"].abs().sum() * 100,
            "underforecast_rate": (group["error"] < 0).mean(),
            "wape": group["absolute_error"].sum() / group["actual"].abs().sum() * 100,
        })
output.mkdir(parents=True, exist_ok=True)
pd.DataFrame(records).to_csv(output / "underforecasting_diagnostics.csv", index=False)
print(f"wrote {len(records)} diagnostic rows")
