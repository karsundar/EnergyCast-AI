"""Validation-only bias correction and scale-aware empirical intervals."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

GROUP = ["target_name", "geography", "horizon"]


@dataclass
class Calibration:
    method: str
    group: pd.DataFrame
    fallback: pd.DataFrame
    scales: pd.Series


def _scores(frame: pd.DataFrame, p10: np.ndarray, p50: np.ndarray,
            p90: np.ndarray) -> dict[str, float]:
    actual = frame["actual"].to_numpy(float)
    error = p50 - actual
    pinball = []
    for q, prediction in ((.1, p10), (.5, p50), (.9, p90)):
        delta = actual - prediction
        pinball.append(np.maximum(q * delta, (q - 1) * delta).mean())
    return {
        "wape": float(np.abs(error).sum() / np.abs(actual).sum() * 100),
        "coverage": float(((actual >= p10) & (actual <= p90)).mean()),
        "mean_interval_width": float(np.mean(p90 - p10)),
        "mean_pinball_loss": float(np.mean(pinball)),
        "bias": float(np.mean(error)),
    }


def fit_calibration(frame: pd.DataFrame, method: str, min_group_samples: int = 12) -> Calibration:
    if method not in {"relative", "scale_normalized"}:
        raise ValueError("method must be relative or scale_normalized")
    required = {*GROUP, "p50", "actual", "target_month"}
    if required - set(frame):
        raise ValueError(f"calibration frame missing {sorted(required - set(frame))}")
    clean = frame.dropna(subset=["p50", "actual"]).copy()
    scales = clean.groupby(["target_name", "geography"], observed=True)["actual"].apply(
        lambda values: float(values.abs().median())
    )
    row_scale = pd.MultiIndex.from_frame(clean[["target_name", "geography"]]).map(scales)
    denominator = np.maximum(np.abs(clean["p50"].to_numpy(float)), 1e-9)
    if method == "scale_normalized":
        denominator = np.maximum(np.asarray(row_scale, dtype=float), 1e-9)
    clean["normalized_error"] = (clean["actual"].to_numpy(float) - clean["p50"].to_numpy(float)) / denominator
    grouped = clean.groupby(GROUP, observed=True)["normalized_error"]
    table = grouped.agg(samples="size", q10=lambda x: x.quantile(.1), q50="median", q90=lambda x: x.quantile(.9)).reset_index()
    table = table[table["samples"] >= min_group_samples]
    fallback = clean.groupby("target_name", observed=True)["normalized_error"].agg(
        samples="size", q10=lambda x: x.quantile(.1), q50="median", q90=lambda x: x.quantile(.9)
    ).reset_index()
    return Calibration(method, table, fallback, scales)


def apply_calibration(frame: pd.DataFrame, calibration: Calibration,
                      correct_bias: bool = True) -> pd.DataFrame:
    result = frame.merge(calibration.group, on=GROUP, how="left", validate="many_to_one")
    fallback = calibration.fallback.set_index("target_name")
    for column in ("q10", "q50", "q90"):
        result[column] = result[column].fillna(result["target_name"].map(fallback[column]))
    if result[["q10", "q50", "q90"]].isna().any().any():
        raise ValueError("no calibration fallback for one or more targets")
    if calibration.method == "relative":
        scale = result["p50"].abs().clip(lower=1e-9).to_numpy(float)
    else:
        keys = pd.MultiIndex.from_frame(result[["target_name", "geography"]])
        scale = np.asarray(keys.map(calibration.scales), dtype=float)
    center = result["p50"].to_numpy(float)
    bias_offset = result["q50"].to_numpy(float) * scale if correct_bias else np.zeros(len(result))
    result["p50"] = center + bias_offset
    result["p10"] = center + result["q10"].to_numpy(float) * scale
    result["p90"] = center + result["q90"].to_numpy(float) * scale
    return result.drop(columns=["samples", "q10", "q50", "q90"])


def select_method(development: pd.DataFrame, min_group_samples: int = 12,
                  objective: str = "pinball") -> tuple[str, dict[str, dict[str, float]]]:
    months = pd.to_datetime(development["target_month"])
    fit = development[months < pd.Timestamp("2020-01-01")]
    holdout = development[months.between(pd.Timestamp("2020-01-01"), pd.Timestamp("2020-12-01"))]
    if fit.empty or holdout.empty:
        raise ValueError("method selection requires pre-2020 fit data and 2020 holdout data")
    scores = {}
    for method in ("relative", "scale_normalized"):
        calibrated = apply_calibration(holdout, fit_calibration(fit, method, min_group_samples))
        scores[method] = _scores(calibrated, calibrated["p10"].to_numpy(),
                                 calibrated["p50"].to_numpy(), calibrated["p90"].to_numpy())
    if objective == "wape":
        selected = min(scores, key=lambda name: scores[name]["wape"])
    elif objective == "pinball":
        selected = min(scores, key=lambda name: (scores[name]["mean_pinball_loss"],
                                                  abs(scores[name]["coverage"] - .8)))
    else:
        raise ValueError("objective must be wape or pinball")
    return selected, scores


def fit_interval_inflation(calibrated_holdout: pd.DataFrame,
                           target_coverage: float = .8) -> pd.DataFrame:
    """Fit geography-specific conformal width multipliers on validation only."""
    frame = calibrated_holdout.copy()
    lower_width = (frame["p50"] - frame["p10"]).clip(lower=1e-9)
    upper_width = (frame["p90"] - frame["p50"]).clip(lower=1e-9)
    frame["nonconformity"] = np.maximum(
        (frame["p50"] - frame["actual"]) / lower_width,
        (frame["actual"] - frame["p50"]) / upper_width,
    ).clip(lower=0)
    return frame.groupby(["target_name", "geography"], observed=True)["nonconformity"].apply(
        lambda values: float(values.quantile(target_coverage, interpolation="higher"))
    ).rename("interval_inflation").reset_index()


def apply_interval_inflation(frame: pd.DataFrame, factors: pd.DataFrame) -> pd.DataFrame:
    result = frame.merge(factors, on=["target_name", "geography"], how="left", validate="many_to_one")
    if result["interval_inflation"].isna().any():
        raise ValueError("missing geography-specific interval inflation factor")
    lower = result["p50"] - result["p10"]
    upper = result["p90"] - result["p50"]
    result["p10"] = result["p50"] - lower * result["interval_inflation"]
    result["p90"] = result["p50"] + upper * result["interval_inflation"]
    return result.drop(columns="interval_inflation")
