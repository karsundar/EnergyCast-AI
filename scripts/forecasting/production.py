"""Atomic, idempotent deterministic EnergyCast V1 publication helpers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import numpy as np
import pandas as pd

KEYS = ["target_name", "origin", "target_month", "horizon", "geography"]
PUBLIC_COLUMNS = [
    *KEYS, "p50", "forecast_issued_at", "data_cutoff", "source_vintage",
    "model_version", "model_experiment", "historical_backtest_wape_pct",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".parquet", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary, index=False, compression="zstd")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".json", mode="w", delete=False) as handle:
        json.dump(value, handle, indent=2, default=str)
        handle.write("\n")
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_source(frame: pd.DataFrame, config: dict[str, Any], as_of: pd.Timestamp) -> dict[str, Any]:
    required = {"month", "geography", "total_energy_mwh", "peak_demand_mw", "completeness_pct"}
    missing = required - set(frame)
    if missing:
        raise ValueError(f"monthly source missing columns: {sorted(missing)}")
    work = frame.copy()
    work["month"] = pd.to_datetime(work.month).dt.to_period("M").dt.to_timestamp()
    work = work[work.month.le(as_of.to_period("M").to_timestamp())]
    if work.empty:
        raise ValueError("monthly source has no observations available at the requested cutoff")
    if work.duplicated(["month", "geography"]).any():
        raise ValueError("monthly source contains duplicate month/geography rows")
    latest = work.month.max()
    latest_rows = work[work.month.eq(latest)]
    expected = set(config["source"]["required_geographies"])
    if set(latest_rows.geography) != expected:
        raise ValueError("latest monthly source does not contain the required nine geographies")
    if latest_rows.completeness_pct.min() < float(config["source"]["minimum_completeness_pct"]):
        raise ValueError("latest monthly source is below the production completeness threshold")
    age = (as_of.tz_localize(None).normalize() - latest).days
    if age > int(config["source"]["maximum_age_days"]):
        raise ValueError(f"monthly source is stale: latest={latest:%Y-%m-%d}, age={age} days")
    return {"data_cutoff": latest, "rows": len(work), "minimum_latest_completeness_pct": float(latest_rows.completeness_pct.min())}


def build_frozen_forecast(bundle: pd.DataFrame, config: dict[str, Any], issued_at: pd.Timestamp,
                          data_cutoff: pd.Timestamp, source_vintage: str) -> pd.DataFrame:
    work = bundle.copy()
    for column in ("origin", "target_month"):
        work[column] = pd.to_datetime(work[column]).dt.tz_localize(None)
    origin = work.origin.max()
    work = work[work.origin.eq(origin)]
    if origin != pd.Timestamp(data_cutoff).to_period("M").to_timestamp():
        raise ValueError(f"candidate origin {origin:%Y-%m} does not match data cutoff {pd.Timestamp(data_cutoff):%Y-%m}")
    outputs = []
    energy = config["models"]["total_energy_mwh"]
    weights = energy["weights"]
    source = work[work.target_name.eq("total_energy_mwh") & work.experiment.isin(weights)]
    pivot = source.pivot(index=KEYS, columns="experiment", values="p50")
    if set(weights) - set(pivot.columns) or pivot.reindex(columns=list(weights)).isna().any().any():
        raise ValueError("E14 candidate bundle is incomplete")
    result = pivot.index.to_frame(index=False)
    result["p50"] = sum(pivot[name] * weight for name, weight in weights.items()).to_numpy()
    result["model_experiment"] = "E14"
    result["historical_backtest_wape_pct"] = float(energy["backtest_wape_pct"])
    outputs.append(result)
    peak = config["models"]["peak_demand_mw"]
    source = work[work.target_name.eq("peak_demand_mw") & work.experiment.eq("E24-B")]
    if source.empty:
        raise ValueError("candidate bundle is missing frozen E24-B peak forecasts")
    result = source[KEYS + ["p50"]].copy()
    result["model_experiment"] = "E24-B"
    result["historical_backtest_wape_pct"] = float(peak["backtest_wape_pct"])
    outputs.append(result)
    forecast = pd.concat(outputs, ignore_index=True)
    forecast["forecast_issued_at"] = issued_at
    forecast["data_cutoff"] = data_cutoff
    forecast["source_vintage"] = source_vintage
    forecast["model_version"] = config["production_version"]
    return forecast[PUBLIC_COLUMNS]


def validate_forecast(frame: pd.DataFrame, horizon: int, geographies: set[str]) -> None:
    if list(frame.columns) != PUBLIC_COLUMNS:
        raise ValueError("public forecast schema differs from the frozen schema")
    if frame.p50.isna().any() or (~np.isfinite(frame.p50)).any() or (frame.p50 <= 0).any():
        raise ValueError("forecast contains non-positive or non-finite values")
    if set(frame.target_name) != {"total_energy_mwh", "peak_demand_mw"}:
        raise ValueError("forecast targets are incomplete")
    if set(frame.geography) != geographies:
        raise ValueError("forecast geographies are incomplete")
    grouped = frame.groupby(["target_name", "geography"], observed=True).horizon.apply(set)
    if not grouped.map(lambda values: values == set(range(1, horizon + 1))).all():
        raise ValueError("forecast does not contain horizons 1-12 for every series")
    expected = pd.to_datetime(frame.origin).dt.to_period("M") + frame.horizon.to_numpy()
    if not (expected == pd.to_datetime(frame.target_month).dt.to_period("M")).all():
        raise ValueError("forecast target-month alignment failed")
    if frame.duplicated(KEYS).any():
        raise ValueError("forecast contains duplicate keys")
    forbidden = {"p10", "p90", "confidence", "coverage"} & set(frame)
    if forbidden:
        raise ValueError(f"research interval columns may not be published: {sorted(forbidden)}")


def forecast_id(frame: pd.DataFrame, source_sha256: str) -> str:
    payload = "|".join([
        str(frame.model_version.iloc[0]), str(frame.origin.iloc[0]),
        str(frame.forecast_issued_at.iloc[0]), source_sha256,
    ])
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def build_shadow_intervals(forecast: pd.DataFrame, band: pd.DataFrame, fallback: pd.DataFrame,
                           scales: pd.DataFrame) -> pd.DataFrame:
    """Apply frozen research candidates without altering the public forecast."""
    result = forecast[KEYS + ["p50", "model_version"]].copy()
    result["horizon_band"] = pd.cut(
        result.horizon, [0, 3, 6, 9, 12], labels=["1-3", "4-6", "7-9", "10-12"]
    ).astype(str)
    result = result.merge(
        band[["target_name", "geography", "horizon_band", "q_low", "q_high", "calibration_method"]],
        on=["target_name", "geography", "horizon_band"], how="left", validate="many_to_one",
    )
    fallback_index = fallback.set_index(["target_name", "geography"])
    keys = pd.MultiIndex.from_frame(result[["target_name", "geography"]])
    for column in ("q_low", "q_high", "calibration_method"):
        result[column] = result[column].fillna(pd.Series(keys.map(fallback_index[column]), index=result.index))
    scale_index = scales.set_index(["target_name", "geography"])["geography_scale"]
    result["geography_scale"] = keys.map(scale_index)
    relative = result.calibration_method.eq("relative_asymmetric")
    multiplier = np.where(relative, result.p50.abs(), result.geography_scale)
    result["shadow_lower"] = result.p50 + result.q_low * multiplier
    result["shadow_upper"] = result.p50 + result.q_high * multiplier
    result["research_only"] = True
    result["validated_probability_claim"] = False
    if result[["shadow_lower", "shadow_upper"]].isna().any().any():
        raise ValueError("frozen shadow calibration is incomplete")
    return result.drop(columns=["q_low", "q_high", "geography_scale"])


def metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    complete = frame.dropna(subset=["actual", "p50"])
    if complete.empty:
        return {"observations": 0}
    error = complete.p50 - complete.actual
    return {
        "observations": len(complete), "wape_pct": float(error.abs().sum() / complete.actual.abs().sum() * 100),
        "mae": float(error.abs().mean()), "rmse": float(np.sqrt(np.square(error).mean())),
        "signed_bias": float(error.mean()),
    }
