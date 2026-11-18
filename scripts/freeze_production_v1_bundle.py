#!/usr/bin/env python3
"""Fit the selected V1 recipes once and freeze an independently loadable bundle."""

from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import warnings

import duckdb
import joblib
import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX

from forecasting.e24 import e24_feature_columns, fit_log_ratio_model_at, query_features
from forecasting.hybrid import fit_residual_model, residual_columns, residual_features
from forecasting.operational_weather import normal_queries
from run_phase5d_e24 import training_samples

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/production_v1_bundle.json"


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fit_holt_winters(history: pd.Series):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return ExponentialSmoothing(
            history.astype(float), trend="add", seasonal="add", seasonal_periods=12,
        ).fit(remove_bias=True)


def fit_sarima(history: pd.Series, settings: dict):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return SARIMAX(
            history.astype(float), order=tuple(settings["order"]),
            seasonal_order=tuple(settings["seasonal_order"]),
            enforce_stationarity=False, enforce_invertibility=False,
        ).fit(disp=False, maxiter=int(settings["maxiter"]))


def versions(names: list[str]) -> dict[str, str]:
    result = {}
    for name in names:
        try:
            result[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    return result


def main() -> int:
    config = json.loads(CONFIG.read_text())
    destination = ROOT / config["output_directory"]
    if destination.exists():
        raise FileExistsError(f"frozen bundle already exists; refusing overwrite: {destination}")
    cutoff = pd.Timestamp(config["training_data_cutoff"])
    issued = pd.Timestamp(config["forecast_issued_at"])
    np.random.seed(int(config["random_seed"]))
    phase4a = json.loads((ROOT / "config/phase4a.json").read_text())
    phase4b = json.loads((ROOT / "config/phase4b.json").read_text())
    e24_config = json.loads((ROOT / "config/phase5d_e24.json").read_text())
    weights = json.loads((ROOT / "reports/phase4b/E14_weights_non-oracle.json").read_text())["weights"]["total_energy_mwh"]
    lambda_rows = json.loads((ROOT / "reports/phase5d/e24/full/lambda_selection.json").read_text())
    shrinkage = next(float(row["selected_lambda"]) for row in lambda_rows
                     if row["target_name"] == "peak_demand_mw" and row["reference_type"] == "holt_winters_oos")

    demand = pd.read_parquet(ROOT / "data/processed/ercot_monthly_targets.parquet")
    demand["month"] = pd.to_datetime(demand.month).dt.to_period("M").dt.to_timestamp()
    demand = demand[demand.month.le(cutoff)].copy()
    if demand.month.max() != cutoff or demand.groupby("geography", observed=True).month.max().ne(cutoff).any():
        raise ValueError("approved training cutoff is not complete for every geography")
    if demand[demand.month.eq(cutoff)].completeness_pct.min() < 99.0:
        raise ValueError("approved training cutoff fails completeness threshold")
    geographies = sorted(demand.geography.unique())

    statistical = {"holt_winters_energy": {}, "sarima_energy": {}, "holt_winters_peak": {}}
    forecasts = []
    for geography in geographies:
        group = demand[demand.geography.eq(geography)].set_index("month").sort_index()
        energy = group.total_energy_mwh.dropna()
        peak = group.peak_demand_mw.dropna()
        hw_energy = fit_holt_winters(energy)
        sarima_energy = fit_sarima(energy, phase4a["sarimax"])
        hw_peak = fit_holt_winters(peak)
        statistical["holt_winters_energy"][geography] = hw_energy
        statistical["sarima_energy"][geography] = sarima_energy
        statistical["holt_winters_peak"][geography] = hw_peak
        months = pd.date_range(cutoff + pd.DateOffset(months=1), periods=12, freq="MS")
        for target, experiment, values in (
            ("total_energy_mwh", "E0", hw_energy.forecast(12)),
            ("total_energy_mwh", "E8", sarima_energy.forecast(12)),
            ("peak_demand_mw", "HW_REFERENCE", hw_peak.forecast(12)),
        ):
            for horizon, (month, value) in enumerate(zip(months, values, strict=True), 1):
                forecasts.append({"target_name": target, "origin": cutoff, "target_month": month,
                                  "horizon": horizon, "geography": geography,
                                  "experiment": experiment, "p50": float(value), "actual": np.nan})
    baselines = pd.DataFrame(forecasts)

    e9_samples = pd.read_parquet(ROOT / "data/processed/phase4b/residual_cache_holt_winters_total_energy_mwh.parquet")
    e9_samples["target_month"] = pd.to_datetime(e9_samples.target_month)
    e9_eligible = e9_samples[e9_samples.target_month.le(cutoff)].dropna()
    e9_model = fit_residual_model(e9_eligible, "level", phase4a["lightgbm"])
    e9_columns = residual_columns(e9_samples)
    e9_query = []
    energy_hw = baselines[baselines.experiment.eq("E0")]
    for row in energy_hw.itertuples(index=False):
        feature = residual_features(demand, row.geography, row.target_name, cutoff,
                                    row.target_month, row.horizon, row.p50, False)
        feature.update({key: getattr(row, key) for key in ("origin", "target_month", "horizon", "geography")})
        feature["baseline_prediction"] = row.p50
        e9_query.append(feature)
    e9_query = pd.DataFrame(e9_query)

    historical = duckdb.sql(
        f"SELECT * FROM read_parquet('{ROOT}/data/processed/weather/seasonal_compatible_era5/year=*/part.parquet')"
    ).df()
    historical["target_month"] = pd.to_datetime(historical.target_month, utc=True).dt.tz_localize(None).dt.to_period("M").dt.to_timestamp()
    operational = pd.read_parquet(ROOT / "reports/phase5c_stage15/full/operational_weather_features.parquet")
    operational["target_month"] = pd.to_datetime(operational.target_month).dt.to_period("M").dt.to_timestamp()
    e24_samples = training_samples(demand, historical, "peak_demand_mw", e24_config, "holt_winters_oos")
    e24_model, e24_label_cutoff = fit_log_ratio_model_at(e24_samples, issued, e24_config["lightgbm"])
    if pd.Timestamp(e24_label_cutoff).tz_localize("UTC") > issued:
        raise ValueError("E24-B fitted labels cross the approved issuance timestamp")
    weather = normal_queries(demand, operational, cutoff, "peak_demand_mw", 12)
    if (pd.to_datetime(weather.weather_available_at, utc=True) > issued).any():
        raise ValueError("E24-B inference weather is unavailable at approved issuance")
    peak_hw = baselines[baselines.experiment.eq("HW_REFERENCE")]
    e24_query = query_features(demand, weather, peak_hw, "peak_demand_mw", cutoff, "holt_winters_oos")
    e24_columns = e24_feature_columns(e24_samples)

    stage_parent = destination.parent
    stage_parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=stage_parent))
    try:
        joblib.dump(statistical, stage / "statistical_models.joblib", compress=3)
        e9_model.booster_.save_model(stage / "e9_energy_residual_lightgbm.txt")
        e24_model.booster_.save_model(stage / "e24b_peak_log_ratio_lightgbm.txt")
        demand.to_parquet(stage / "training_context.parquet", index=False, compression="zstd")
        baselines.to_parquet(stage / "reference_forecasts.parquet", index=False, compression="zstd")
        e9_query.to_parquet(stage / "e9_inference_features.parquet", index=False, compression="zstd")
        e24_query.to_parquet(stage / "e24b_inference_features.parquet", index=False, compression="zstd")
        shutil.copytree(Path(config["chronos_snapshot"]), stage / "chronos2", symlinks=False)
        chronos_versions = json.loads(subprocess.check_output([
            str(ROOT / config["chronos_environment"]), "-c",
            "import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in ['torch','chronos-forecasting','transformers','huggingface-hub']}))",
        ], text=True))
        feature_schema = {
            "e9": e9_columns, "e24b": e24_columns, "e24b_shrinkage_lambda": shrinkage,
            "e9_transform": "level", "chronos_context_columns": ["geography", "month", "total_energy_mwh"],
        }
        (stage / "feature_schema.json").write_text(json.dumps(feature_schema, indent=2) + "\n")
        artifacts = {}
        for path in sorted(item for item in stage.rglob("*") if item.is_file()):
            artifacts[str(path.relative_to(stage))] = {"sha256": checksum(path), "bytes": path.stat().st_size}
        manifest = {
            "bundle_version": config["bundle_version"], "created_at": pd.Timestamp.now(tz="UTC").isoformat(),
            "training_data_cutoff": str(cutoff.date()), "forecast_issued_at": issued.isoformat(),
            "component_training_cutoffs": {
                "statistical_history": str(cutoff.date()),
                "e9_residual_target_month": str(e9_eligible.target_month.max().date()),
                "e24b_label_available_at": str(e24_label_cutoff),
                "chronos_context": str(cutoff.date()),
            },
            "leakage_audit": {
                "maximum_demand_context_month": str(demand.month.max().date()),
                "maximum_e9_residual_target_month": str(e9_eligible.target_month.max().date()),
                "maximum_e24b_label_available_at": str(e24_label_cutoff),
                "maximum_weather_available_at": pd.to_datetime(weather.weather_available_at, utc=True).max().isoformat(),
                "all_cutoffs_at_or_before_issuance": True,
            },
            "models": {"energy": "E14", "peak": "E24-B"}, "e14_weights": weights,
            "random_seed": config["random_seed"], "forecast_horizon_months": 12,
            "geographies": geographies, "feature_schema": feature_schema,
            "hyperparameters": {"sarima": phase4a["sarimax"], "lightgbm_e9": phase4a["lightgbm"],
                                "lightgbm_e24b": e24_config["lightgbm"], "chronos2": phase4b["chronos2"]},
            "dependencies": {"core": versions(["pandas", "numpy", "scipy", "statsmodels", "lightgbm", "joblib", "pyarrow", "duckdb"]),
                             "chronos": chronos_versions, "python": platform.python_version()},
            "source_checksums": {
                "monthly_targets": checksum(ROOT / "data/processed/ercot_monthly_targets.parquet"),
                "e9_oos_residuals": checksum(ROOT / "data/processed/phase4b/residual_cache_holt_winters_total_energy_mwh.parquet"),
                "e24b_oos_residuals": checksum(ROOT / "data/processed/phase5d/e25_oos_residuals_peak_demand_mw.parquet"),
            },
            "artifacts": artifacts, "public_probabilistic_intervals": False,
        }
        (stage / "bundle_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        os.replace(stage, destination)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(json.dumps({"status": "frozen", "bundle": str(destination), "artifacts": len(artifacts),
                      "bytes": sum(item["bytes"] for item in artifacts.values())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
