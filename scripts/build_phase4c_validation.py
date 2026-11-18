#!/usr/bin/env python3
"""Build development-only baseline predictions from existing OOS residual caches."""

import json
from pathlib import Path
import numpy as np
import pandas as pd

from forecasting.hybrid import apply_residual_model, fit_residual_model, residual_columns, select_residual_transform

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "processed" / "phase4b"
OUTPUT = ROOT / "data" / "processed" / "phase4c" / "validation_predictions.parquet"
BASELINES = {"holt_winters": "E0", "sarimax_oracle": "E1", "sarima": "E8"}


def main() -> int:
    model_config = json.loads((ROOT / "config" / "phase4a.json").read_text())["lightgbm"]
    model_config["n_jobs"] = 2
    frames = []
    for baseline, experiment in BASELINES.items():
        for target in ("total_energy_mwh", "peak_demand_mw"):
            frame = pd.read_parquet(SOURCE / f"residual_cache_{baseline}_{target}.parquet")
            frame = frame[frame["target_month"].between("2018-01-01", "2020-12-01")].copy()
            frame = frame.rename(columns={"baseline_prediction": "p50"})
            frame["experiment"] = experiment
            frame["target_name"] = target
            frame["oracle_weather"] = experiment == "E1"
            frames.append(frame[["experiment", "target_name", "origin", "target_month", "horizon",
                                 "geography", "p50", "actual", "oracle_weather"]])
    for baseline, experiment in (("holt_winters", "E9"), ("sarimax_oracle", "E10")):
        for target in ("total_energy_mwh", "peak_demand_mw"):
            samples = pd.read_parquet(SOURCE / f"residual_cache_{baseline}_{target}.parquet")
            transform, _ = select_residual_transform(samples, model_config, "2020-12-01")
            rows = []
            for origin, forecast_rows in samples[
                samples["target_month"].between("2018-01-01", "2020-12-01")
            ].groupby("origin", observed=True):
                available = samples[samples["target_month"] <= origin].dropna()
                if available.empty:
                    continue
                model = fit_residual_model(available, transform, model_config)
                baseline_values = forecast_rows["baseline_prediction"].to_numpy(float)
                corrected = apply_residual_model(model, forecast_rows[residual_columns(samples)],
                                                  baseline_values, transform)
                block = forecast_rows.copy()
                block["p50"] = corrected
                block["experiment"] = experiment
                block["target_name"] = target
                block["oracle_weather"] = experiment == "E10"
                rows.append(block)
            if rows:
                corrected_frame = pd.concat(rows, ignore_index=True)
                frames.append(corrected_frame[["experiment", "target_name", "origin", "target_month", "horizon",
                                               "geography", "p50", "actual", "oracle_weather"]])
    output = pd.concat(frames, ignore_index=True)
    if not (output["target_month"] > output["origin"]).all() or output["target_month"].max() > pd.Timestamp("2020-12-01"):
        raise RuntimeError("validation predictions violate temporal boundary")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    output.to_parquet(OUTPUT, index=False, compression="zstd")
    print(f"wrote {len(output)} validation predictions to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
