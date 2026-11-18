#!/usr/bin/env python3
"""Inference-only execution of the fitted deterministic EnergyCast V1 bundle."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

import joblib
from lightgbm import Booster
import numpy as np
import pandas as pd

from forecasting.frozen_bundle import sha256, validate_bundle
from forecasting.production import build_frozen_forecast, validate_forecast

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-output", type=Path)
    options = parser.parse_args()
    bundle = options.bundle.resolve()
    manifest = validate_bundle(bundle)
    schema = manifest["feature_schema"]
    models = joblib.load(bundle / "statistical_models.joblib")
    reference = pd.read_parquet(bundle / "reference_forecasts.parquet")
    origin = pd.Timestamp(manifest["training_data_cutoff"])
    records = []
    for geography in manifest["geographies"]:
        months = pd.date_range(origin + pd.DateOffset(months=1), periods=12, freq="MS")
        for experiment, key in (("E0", "holt_winters_energy"), ("E8", "sarima_energy")):
            values = models[key][geography].forecast(12)
            for horizon, (month, value) in enumerate(zip(months, values, strict=True), 1):
                records.append({"target_name": "total_energy_mwh", "origin": origin,
                                "target_month": month, "horizon": horizon, "geography": geography,
                                "experiment": experiment, "p50": float(value)})
    e9_features = pd.read_parquet(bundle / "e9_inference_features.parquet")
    e9 = Booster(model_file=str(bundle / "e9_energy_residual_lightgbm.txt"))
    correction = e9.predict(e9_features[schema["e9"]])
    e9_output = e9_features[["origin", "target_month", "horizon", "geography"]].copy()
    e9_output["target_name"] = "total_energy_mwh"
    e9_output["experiment"] = "E9"
    e9_output["p50"] = e9_features.baseline_prediction.to_numpy() + correction
    records.extend(e9_output.to_dict("records"))
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as handle:
        chronos_path = Path(handle.name)
    try:
        subprocess.run([
            str(ROOT / ".venv-chronos2/bin/python"), str(ROOT / "scripts/infer_frozen_chronos.py"),
            "--bundle", str(bundle), "--output", str(chronos_path),
        ], check=True, cwd=ROOT)
        chronos = pd.read_parquet(chronos_path)
    finally:
        chronos_path.unlink(missing_ok=True)
    chronos["origin"] = origin
    chronos["horizon"] = chronos.groupby("geography", observed=True).cumcount() + 1
    chronos["target_name"] = "total_energy_mwh"
    chronos["experiment"] = "E11"
    records.extend(chronos[["target_name", "origin", "target_month", "horizon", "geography", "experiment", "p50"]].to_dict("records"))
    e24_features = pd.read_parquet(bundle / "e24b_inference_features.parquet")
    e24 = Booster(model_file=str(bundle / "e24b_peak_log_ratio_lightgbm.txt"))
    ratio = e24.predict(e24_features[schema["e24b"]])
    peak = e24_features[["origin", "target_month", "horizon", "geography"]].copy()
    peak["target_name"] = "peak_demand_mw"
    peak["experiment"] = "E24-B"
    peak["p50"] = e24_features.reference_value.to_numpy() * np.exp(float(schema["e24b_shrinkage_lambda"]) * ratio)
    records.extend(peak.to_dict("records"))
    candidates = pd.DataFrame(records)
    if options.candidate_output:
        options.candidate_output.parent.mkdir(parents=True, exist_ok=True)
        candidates.to_parquet(options.candidate_output, index=False, compression="zstd")
    production_config = json.loads((ROOT / "config/production_v1.json").read_text())
    production_config["production_version"] = manifest["bundle_version"]
    production_config["models"]["total_energy_mwh"]["weights"] = manifest["e14_weights"]
    forecast = build_frozen_forecast(
        candidates, production_config, pd.Timestamp(manifest["forecast_issued_at"]), origin,
        manifest["source_checksums"]["monthly_targets"][:16],
    )
    validate_forecast(forecast, 12, set(manifest["geographies"]))
    options.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = options.output.with_suffix(options.output.suffix + ".tmp")
    forecast.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, options.output)
    print(json.dumps({"status": "complete", "rows": len(forecast), "bundle": manifest["bundle_version"],
                      "output_sha256": sha256(options.output), "public_intervals": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
