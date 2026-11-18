"""Pre-test-safe blocked calibration remedies for frozen V1 forecasts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


BASE_GROUP = ["target_name", "geography"]
BAND_GROUP = ["target_name", "geography", "horizon_band"]


@dataclass(frozen=True)
class RemedyCalibration:
    method: str
    by_band: pd.DataFrame
    fallback: pd.DataFrame
    scales: pd.DataFrame
    target_coverage: float


def add_horizon_band(frame: pd.DataFrame, bands: dict[str, list[int]]) -> pd.DataFrame:
    result = frame.copy()
    result["horizon_band"] = pd.NA
    for label, (lower, upper) in bands.items():
        result.loc[result.horizon.between(lower, upper), "horizon_band"] = label
    if result.horizon_band.isna().any():
        raise ValueError("one or more horizons have no predefined calibration band")
    return result


def label_available_at(target_month: pd.Series) -> pd.Series:
    return pd.to_datetime(target_month) + pd.offsets.MonthEnd(0) + pd.Timedelta(days=1)


def _summary(values: pd.Series, method: str, coverage: float) -> pd.Series:
    if method.endswith("symmetric"):
        radius = float(values.abs().quantile(coverage, interpolation="higher"))
        low, high = -radius, radius
    else:
        tail = (1.0 - coverage) / 2.0
        low = min(float(values.quantile(tail, interpolation="lower")), 0.0)
        high = max(float(values.quantile(1.0 - tail, interpolation="higher")), 0.0)
    return pd.Series({"q_low": low, "q_high": high, "samples": int(values.size)})


def fit_remedy(
    frame: pd.DataFrame,
    method: str,
    target_coverage: float,
    minimum_band_samples: int,
) -> RemedyCalibration:
    if method not in {
        "scale_normalized_symmetric", "scale_normalized_asymmetric", "relative_asymmetric",
    }:
        raise ValueError(f"unsupported remedy method: {method}")
    clean = frame.dropna(subset=["p50", "actual", "horizon_band"]).copy()
    if clean.empty:
        raise ValueError("no observable calibration residuals")
    scales = clean.groupby(BASE_GROUP, observed=True).actual.apply(
        lambda values: float(values.abs().median())
    ).rename("geography_scale").reset_index()
    clean = clean.merge(scales, on=BASE_GROUP, validate="many_to_one")
    denominator = clean.p50.abs().clip(lower=1e-9)
    if method.startswith("scale_normalized"):
        denominator = clean.geography_scale.clip(lower=1e-9)
    clean["normalized_error"] = (clean.actual - clean.p50) / denominator
    by_band = (
        clean.groupby(BAND_GROUP, observed=True).normalized_error
        .apply(_summary, method, target_coverage).unstack().reset_index()
    )
    by_band = by_band[by_band.samples.ge(minimum_band_samples)].copy()
    fallback = (
        clean.groupby(BASE_GROUP, observed=True).normalized_error
        .apply(_summary, method, target_coverage).unstack().reset_index()
    )
    return RemedyCalibration(method, by_band, fallback, scales, target_coverage)


def apply_remedy(frame: pd.DataFrame, fitted: RemedyCalibration) -> pd.DataFrame:
    result = frame.merge(fitted.scales, on=BASE_GROUP, how="left", validate="many_to_one")
    result = result.merge(
        fitted.by_band[BAND_GROUP + ["q_low", "q_high", "samples"]],
        on=BAND_GROUP, how="left", validate="many_to_one",
    )
    used_band = result.q_low.notna()
    fallback = fitted.fallback.set_index(BASE_GROUP)
    keys = pd.MultiIndex.from_frame(result[BASE_GROUP])
    for column in ("q_low", "q_high", "samples"):
        result[column] = result[column].fillna(
            pd.Series(keys.map(fallback[column]), index=result.index)
        )
    if result[["q_low", "q_high", "geography_scale"]].isna().any().any():
        raise ValueError("missing same-geography calibration fallback")
    scale = result.p50.abs().clip(lower=1e-9)
    if fitted.method.startswith("scale_normalized"):
        scale = result.geography_scale
    result["p10"] = result.p50 + result.q_low * scale
    result["p90"] = result.p50 + result.q_high * scale
    result["calibration_scope"] = np.where(used_band, "geography_horizon_band", "geography_fallback")
    result["calibration_method"] = fitted.method
    result["target_coverage"] = fitted.target_coverage
    if not (result.p10 <= result.p50).all() or not (result.p50 <= result.p90).all():
        raise AssertionError("calibration remedy produced crossing quantiles")
    return result.drop(columns=["q_low", "q_high", "samples", "geography_scale"])


def score_intervals(frame: pd.DataFrame, origin_period: str) -> pd.DataFrame:
    work = frame.dropna(subset=["actual", "p10", "p50", "p90"]).copy()
    work["scope"] = np.where(work.geography.eq("ERCOT_SYSTEM"), "system", "regional")
    work["origin_period"] = origin_period
    work["interval_width"] = work.p90 - work.p10
    rows: list[dict[str, Any]] = []
    dimensions = {
        "overall": ["target_name", "scope", "calibration_method"],
        "horizon_band": ["target_name", "scope", "calibration_method", "horizon_band"],
        "geography": ["target_name", "calibration_method", "geography"],
        "origin_period": ["target_name", "scope", "calibration_method", "origin_period"],
        "growth_regime": ["target_name", "scope", "calibration_method", "growth_regime"],
    }
    for breakdown, groups in dimensions.items():
        for keys, group in work.groupby(groups, observed=True):
            keys = keys if isinstance(keys, tuple) else (keys,)
            pinballs = []
            for quantile, column in ((.1, "p10"), (.5, "p50"), (.9, "p90")):
                delta = group.actual - group[column]
                pinballs.append(np.maximum(quantile * delta, (quantile - 1) * delta).mean())
            scale = max(float(group.actual.abs().median()), 1e-9)
            rows.append({
                "breakdown": breakdown, "blocked_fold": origin_period,
                **dict(zip(groups, keys, strict=True)),
                "observations": len(group),
                "coverage": float(group.actual.between(group.p10, group.p90).mean()),
                "mean_interval_width": float(group.interval_width.mean()),
                "mean_pinball_loss": float(np.mean(pinballs)),
                "normalized_pinball_loss": float(np.mean(pinballs) / scale),
            })
    return pd.DataFrame(rows)


def select_methods(
    metrics: pd.DataFrame, target_coverage: float, coverage_penalty_weight: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    overall = metrics[metrics.breakdown.eq("overall")].copy()
    aggregated = overall.groupby(
        ["target_name", "scope", "calibration_method"], observed=True
    ).apply(
        lambda group: pd.Series({
            "folds": group.blocked_fold.nunique(),
            "coverage": np.average(group.coverage, weights=group.observations),
            "normalized_pinball_loss": np.average(
                group.normalized_pinball_loss, weights=group.observations
            ),
        }), include_groups=False,
    ).reset_index()
    aggregated["selection_score"] = (
        aggregated.normalized_pinball_loss
        + coverage_penalty_weight * (aggregated.coverage - target_coverage).abs()
    )
    selected = (
        aggregated.sort_values(["selection_score", "normalized_pinball_loss"])
        .groupby(["target_name", "scope"], observed=True).head(1).reset_index(drop=True)
    )
    return selected, aggregated
