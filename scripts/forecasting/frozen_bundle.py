"""Strict validation for an immutable EnergyCast fitted-model bundle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_bundle(bundle: Path) -> dict[str, Any]:
    manifest_path = bundle / "bundle_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("bundle manifest is missing")
    manifest = json.loads(manifest_path.read_text())
    required = {
        "statistical_models.joblib", "e9_energy_residual_lightgbm.txt",
        "e24b_peak_log_ratio_lightgbm.txt", "training_context.parquet",
        "reference_forecasts.parquet", "e9_inference_features.parquet",
        "e24b_inference_features.parquet", "feature_schema.json",
        "chronos2/model.safetensors", "chronos2/config.json",
    }
    declared = set(manifest.get("artifacts", {}))
    if required - declared:
        raise ValueError(f"bundle is incomplete: {sorted(required - declared)}")
    for relative, expected in manifest["artifacts"].items():
        path = bundle / relative
        if not path.is_file():
            raise ValueError(f"bundle artifact is missing: {relative}")
        if path.stat().st_size != int(expected["bytes"]) or sha256(path) != expected["sha256"]:
            raise ValueError(f"bundle artifact checksum mismatch: {relative}")
    schema = json.loads((bundle / "feature_schema.json").read_text())
    if schema != manifest["feature_schema"]:
        raise ValueError("feature schema does not match manifest")
    if manifest.get("public_probabilistic_intervals") is not False:
        raise ValueError("bundle incorrectly enables public probabilistic intervals")
    issued = pd.Timestamp(manifest["forecast_issued_at"])
    if issued.tzinfo is None:
        issued = issued.tz_localize("UTC")
    cutoffs = manifest.get("leakage_audit", {})
    if not cutoffs.get("all_cutoffs_at_or_before_issuance"):
        raise ValueError("bundle leakage audit is missing or failed")
    for name in ("maximum_demand_context_month", "maximum_e9_residual_target_month",
                 "maximum_e24b_label_available_at", "maximum_weather_available_at"):
        value = pd.Timestamp(cutoffs[name])
        value = value.tz_localize("UTC") if value.tzinfo is None else value.tz_convert("UTC")
        if value > issued:
            raise ValueError(f"bundle cutoff crosses issuance: {name}")
    return manifest
