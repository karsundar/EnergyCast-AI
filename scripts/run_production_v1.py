#!/usr/bin/env python3
"""Run, validate, monitor, or roll back deterministic EnergyCast V1 forecasts."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pandas as pd

from forecasting.production import (
    atomic_json, atomic_parquet, build_frozen_forecast, build_shadow_intervals, forecast_id, metrics,
    sha256, validate_forecast, validate_source,
)
from forecasting.frozen_bundle import validate_bundle

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/production_v1.json"


def load_config() -> dict:
    config = json.loads(CONFIG_PATH.read_text())
    weights = json.loads((ROOT / config["models"]["total_energy_mwh"]["artifact"]).read_text())["weights"]["total_energy_mwh"]
    config["models"]["total_energy_mwh"]["weights"] = weights
    return config


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("action", choices=("refresh", "prepare-replay", "validate-bundle", "run", "validate", "monitor", "rollback", "schedule-preview"))
    result.add_argument("--candidate-bundle", type=Path)
    result.add_argument("--bundle", type=Path)
    result.add_argument("--issued-at")
    result.add_argument("--as-of")
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--forecast-id")
    result.add_argument("--origin", default="2025-04-01")
    result.add_argument("--simulate-failure", action="store_true", help=argparse.SUPPRESS)
    return result


def locations(config: dict) -> tuple[Path, Path]:
    return ROOT / config["paths"]["root"], ROOT / config["paths"]["reports"]


def run(options: argparse.Namespace, config: dict) -> dict:
    production, reports = locations(config)
    source_path = ROOT / config["source"]["monthly_targets"]
    model_manifest = None
    if options.candidate_bundle:
        bundle_path = options.candidate_bundle
    else:
        model_bundle = (options.bundle or ROOT / config["paths"]["model_bundle"]).resolve()
        model_manifest = validate_bundle(model_bundle)
        bundle_path = ROOT / config["paths"]["candidate_bundle"]
        reference_output = reports / "frozen_bundle_reference.parquet"
        subprocess.run([
            sys.executable, "scripts/run_frozen_bundle_inference.py", "--bundle", str(model_bundle),
            "--output", str(reference_output), "--candidate-output", str(bundle_path),
        ], cwd=ROOT, check=True)
    default_issued = model_manifest["forecast_issued_at"] if model_manifest else pd.Timestamp.now(tz="UTC")
    issued = pd.Timestamp(options.issued_at or default_issued)
    if issued.tzinfo is None:
        issued = issued.tz_localize("UTC")
    default_as_of = model_manifest["training_data_cutoff"] if model_manifest else issued
    as_of = pd.Timestamp(options.as_of or default_as_of).tz_localize(None)
    source = pd.read_parquet(source_path)
    status = validate_source(source, config, as_of)
    bundle = pd.read_parquet(bundle_path)
    source_digest = sha256(source_path)
    forecast = build_frozen_forecast(bundle, config, issued, status["data_cutoff"], source_digest[:16])
    validate_forecast(forecast, int(config["forecast_horizon_months"]), set(config["source"]["required_geographies"]))
    identifier = forecast_id(forecast, source_digest)
    history = production / "history" / f"{identifier}.parquet"
    manifest = production / "history" / f"{identifier}.json"
    if history.exists() and manifest.exists():
        return {"status": "cache_hit", "forecast_id": identifier, "path": str(history)}
    metadata = {
        "status": "validated_dry_run" if options.dry_run else "published",
        "forecast_id": identifier, "issued_at": issued, "data_cutoff": status["data_cutoff"],
        "source_sha256": source_digest, "candidate_bundle": str(bundle_path),
        "candidate_bundle_sha256": sha256(bundle_path), "rows": len(forecast),
        "fitted_model_bundle": (str(model_bundle) if model_manifest else None),
        "fitted_model_bundle_version": (model_manifest["bundle_version"] if model_manifest else None),
        "models": {key: value["experiment"] for key, value in config["models"].items()},
        "probabilistic_intervals_published": False,
    }
    if options.simulate_failure:
        raise RuntimeError("simulated failure before atomic publication")
    if options.dry_run:
        atomic_parquet(forecast, reports / "dry_run_forecast.parquet")
        atomic_json(metadata, reports / "dry_run_report.json")
    else:
        atomic_parquet(forecast, history)
        atomic_json(metadata, manifest)
        atomic_parquet(forecast, production / "current.parquet")
        atomic_json(metadata, production / "current.json")
    if config["shadow_calibration"]["enabled_internal"]:
        remedy = ROOT / "reports/v1/calibration_remedy/full"
        shadow = build_shadow_intervals(
            forecast, pd.read_csv(remedy / "frozen_band_calibration.csv"),
            pd.read_csv(remedy / "frozen_fallback_calibration.csv"),
            pd.read_csv(remedy / "frozen_scales_calibration.csv"),
        )
        shadow_path = (reports / "dry_run_shadow_intervals.parquet" if options.dry_run
                       else production / "shadow" / f"{identifier}.parquet")
        atomic_parquet(shadow, shadow_path)
    report_path = reports / ("dry_run_forecast_report.md" if options.dry_run else "current_forecast_report.md")
    report_path.write_text(
        "# EnergyCast V1 deterministic forecast\n\n"
        f"- Forecast ID: `{identifier}`\n- Issued: {issued}\n- Data cutoff: {status['data_cutoff']:%Y-%m-%d}\n"
        f"- Model version: `{config['production_version']}`\n- Rows: {len(forecast)}\n"
        "- Models: E14 non-oracle energy; E24-B peak\n"
        "- Probabilistic intervals: disabled; internal shadow candidates are research-only\n",
        encoding="utf-8",
    )
    return metadata


def refresh_sources(config: dict) -> dict:
    """Refresh ERCOT caches and rebuild normalized data with rollback on failure."""
    source = ROOT / config["source"]["monthly_targets"]
    backup = source.with_suffix(".production-refresh-backup.parquet")
    if source.exists():
        shutil.copy2(source, backup)
    commands = [
        [sys.executable, "scripts/download_datasets.py", "--dataset", "ercot_hourly_load",
         "--ledger", "reports/production/v1/ercot_refresh_acquisition.json"],
        [sys.executable, "scripts/normalize_phase1c.py"],
    ]
    try:
        for command in commands:
            subprocess.run(command, cwd=ROOT, check=True)
        frame = pd.read_parquet(source)
        status = validate_source(frame, config, pd.Timestamp.now())
        backup.unlink(missing_ok=True)
        return {"status": "refreshed", **status, "commands": commands}
    except Exception:
        if backup.exists():
            os.replace(backup, source)
        raise


def prepare_replay(config: dict, origin: str, destination: Path | None) -> dict:
    timestamp = pd.Timestamp(origin).to_period("M").to_timestamp()
    phase4c = pd.read_parquet(ROOT / "data/processed/phase4c/chronos_test_predictions_comparable.parquet")
    energy = phase4c[
        phase4c.origin.eq(timestamp) & phase4c.target_name.eq("total_energy_mwh")
        & phase4c.experiment.isin(config["models"]["total_energy_mwh"]["candidate_experiments"])
    ][["target_name", "origin", "target_month", "horizon", "geography", "experiment", "p50"]]
    e24 = pd.read_parquet(ROOT / "data/processed/phase5d/e24/full/E24_predictions.parquet")
    peak = e24[
        e24.origin.eq(timestamp) & e24.target_name.eq("peak_demand_mw") & e24.experiment.eq("E24-B")
    ][["target_name", "origin", "target_month", "horizon", "geography", "experiment", "p50"]]
    bundle = pd.concat([energy, peak], ignore_index=True)
    expected = 9 * 12 * 5
    if len(bundle) != expected or bundle.duplicated(["target_name", "origin", "target_month", "horizon", "geography", "experiment"]).any():
        raise ValueError(f"replay candidate bundle is incomplete: rows={len(bundle)}, expected={expected}")
    path = destination or ROOT / config["paths"]["candidate_bundle"]
    atomic_parquet(bundle, path)
    return {"status": "prepared", "replay_only": True, "origin": str(timestamp.date()), "rows": len(bundle), "path": str(path)}


def validate_current(config: dict) -> dict:
    production, _ = locations(config)
    frame = pd.read_parquet(production / "current.parquet")
    validate_forecast(frame, int(config["forecast_horizon_months"]), set(config["source"]["required_geographies"]))
    return {"status": "valid", "rows": len(frame), "forecast_id": json.loads((production / "current.json").read_text())["forecast_id"]}


def monitor(config: dict) -> dict:
    production, reports = locations(config)
    forecast = pd.read_parquet(production / "current.parquet")
    actual = pd.read_parquet(ROOT / config["source"]["monthly_targets"])
    actual["target_month"] = pd.to_datetime(actual.month).dt.to_period("M").dt.to_timestamp()
    long = actual.melt(id_vars=["target_month", "geography"], value_vars=["total_energy_mwh", "peak_demand_mw"], var_name="target_name", value_name="actual")
    joined = forecast.merge(long, on=["target_name", "target_month", "geography"], how="left", validate="one_to_one")
    matured = joined.dropna(subset=["actual"])
    atomic_parquet(joined, production / "monitoring" / "forecast_vs_actual.parquet")
    report = {"status": "complete", "deterministic": metrics(matured), "shadow_intervals": "research-only; not published", "matured_rows": len(matured)}
    atomic_json(report, reports / "monitoring_report.json")
    return report


def rollback(config: dict, identifier: str | None) -> dict:
    if not identifier:
        raise ValueError("rollback requires --forecast-id")
    production, _ = locations(config)
    source = production / "history" / f"{identifier}.parquet"
    manifest = production / "history" / f"{identifier}.json"
    if not source.exists() or not manifest.exists():
        raise FileNotFoundError(f"validated forecast history not found: {identifier}")
    frame = pd.read_parquet(source)
    validate_forecast(frame, int(config["forecast_horizon_months"]), set(config["source"]["required_geographies"]))
    atomic_parquet(frame, production / "current.parquet")
    metadata = json.loads(manifest.read_text())
    metadata["rollback_selected"] = True
    atomic_json(metadata, production / "current.json")
    return {"status": "rolled_back", "forecast_id": identifier}


def schedule_preview(config: dict) -> dict:
    command = f"cd {ROOT} && {ROOT}/.venv/bin/python scripts/run_production_v1.py run >> reports/production/v1/scheduler.log 2>&1"
    return {"status": "preview_only_not_installed", "timezone": config["schedule"]["timezone"], "cron": f"{config['schedule']['cron']} {command}", "activation_requires_approval": True}


def main() -> int:
    options = parser().parse_args()
    config = load_config()
    _, reports = locations(config)
    reports.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(reports / "production.log")])
    try:
        if options.action == "refresh": result = refresh_sources(config)
        elif options.action == "validate-bundle":
            manifest = validate_bundle(ROOT / config["paths"]["model_bundle"])
            result = {"status": "valid", "bundle_version": manifest["bundle_version"],
                      "artifact_count": len(manifest["artifacts"])}
        elif options.action == "prepare-replay": result = prepare_replay(config, options.origin, options.candidate_bundle)
        elif options.action == "run": result = run(options, config)
        elif options.action == "validate": result = validate_current(config)
        elif options.action == "monitor": result = monitor(config)
        elif options.action == "rollback": result = rollback(config, options.forecast_id)
        else: result = schedule_preview(config)
        logging.info(json.dumps(result, default=str))
        print(json.dumps(result, indent=2, default=str))
        return 0
    except Exception as error:
        logging.exception("production action failed without changing the current forecast")
        print(json.dumps({"status": "failed", "error": str(error)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
