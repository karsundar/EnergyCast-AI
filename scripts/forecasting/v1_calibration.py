"""Chronological, geography-scale-aware EnergyCast V1 calibration."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


KEYS = ["target_name", "origin", "target_month", "horizon", "geography"]
GROUP = ["target_name", "geography", "horizon"]
FALLBACK_GROUP = ["target_name", "geography"]


@dataclass(frozen=True)
class V1Calibration:
    by_horizon: pd.DataFrame
    by_geography: pd.DataFrame
    scales: pd.DataFrame
    target_coverage: float
    minimum_horizon_samples: int


def _quantiles(values: pd.Series, target_coverage: float) -> pd.Series:
    radius = float(values.abs().quantile(target_coverage, interpolation="higher"))
    return pd.Series({
        "q_low": -radius,
        "q_high": radius,
        "samples": int(values.size),
    })


def fit_scale_aware_intervals(
    calibration: pd.DataFrame,
    target_coverage: float = 0.8,
    minimum_horizon_samples: int = 8,
) -> V1Calibration:
    required = {*KEYS, "p50", "actual"}
    if required - set(calibration):
        raise ValueError(f"calibration rows missing {sorted(required - set(calibration))}")
    frame = calibration.dropna(subset=["p50", "actual"]).copy()
    if frame.empty or not frame.target_month.between("2020-01-01", "2020-12-01").all():
        raise ValueError("V1 interval calibration must use only the reserved 2020 window")
    scales = frame.groupby(FALLBACK_GROUP, observed=True).actual.apply(
        lambda values: float(values.abs().median())
    ).rename("scale").reset_index()
    frame = frame.merge(scales, on=FALLBACK_GROUP, validate="many_to_one")
    if (frame.scale <= 0).any():
        raise ValueError("nonpositive geography calibration scale")
    # Relative nonconformity scales with the forecast at application time and
    # remains grouped by geography; unscaled errors are never pooled.
    frame["normalized_error"] = (
        (frame.actual - frame.p50) / frame.p50.abs().clip(lower=1e-9)
    )
    by_horizon = (
        frame.groupby(GROUP, observed=True).normalized_error
        .apply(_quantiles, target_coverage).unstack().reset_index()
    )
    by_horizon = by_horizon[by_horizon.samples.ge(minimum_horizon_samples)].copy()
    by_geography = (
        frame.groupby(FALLBACK_GROUP, observed=True).normalized_error
        .apply(_quantiles, target_coverage).unstack().reset_index()
    )
    return V1Calibration(
        by_horizon, by_geography, scales, target_coverage, minimum_horizon_samples
    )


def apply_scale_aware_intervals(frame: pd.DataFrame, fitted: V1Calibration) -> pd.DataFrame:
    result = frame.merge(fitted.scales, on=FALLBACK_GROUP, how="left", validate="many_to_one")
    result = result.merge(
        fitted.by_horizon[GROUP + ["q_low", "q_high", "samples"]],
        on=GROUP, how="left", validate="many_to_one",
    )
    used_horizon_calibration = result["q_low"].notna()
    fallback = fitted.by_geography.set_index(FALLBACK_GROUP)
    keys = pd.MultiIndex.from_frame(result[FALLBACK_GROUP])
    for column in ("q_low", "q_high", "samples"):
        values = keys.map(fallback[column])
        result[column] = result[column].fillna(pd.Series(values, index=result.index))
    if result[["scale", "q_low", "q_high"]].isna().any().any():
        raise ValueError("missing geography-specific V1 calibration fallback")
    forecast_scale = result.p50.abs().clip(lower=1e-9)
    result["p10"] = result.p50 + result.q_low * forecast_scale
    result["p90"] = result.p50 + result.q_high * forecast_scale
    result["calibration_scope"] = np.where(
        used_horizon_calibration, "geography_horizon", "geography_fallback"
    )
    result["calibration_target_coverage"] = fitted.target_coverage
    return result.drop(columns=["scale", "q_low", "q_high", "samples"])


def interval_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    complete = frame.dropna(subset=["actual", "p10", "p50", "p90"]).copy()
    complete["scope"] = np.where(complete.geography.eq("ERCOT_SYSTEM"), "system", "regional")
    rows = []
    dimensions = {"overall": ["scope"], "horizon": ["scope", "horizon"]}
    for breakdown, extra in dimensions.items():
        groups = ["target_name", *extra]
        for keys, group in complete.groupby(groups, observed=True):
            keys = keys if isinstance(keys, tuple) else (keys,)
            error = group.p50 - group.actual
            pinball = []
            for quantile, column in ((.1, "p10"), (.5, "p50"), (.9, "p90")):
                delta = group.actual - group[column]
                pinball.append(np.maximum(quantile * delta, (quantile - 1) * delta).mean())
            rows.append({
                "breakdown": breakdown, **dict(zip(groups, keys, strict=True)),
                "observations": len(group),
                "wape": float(error.abs().sum() / group.actual.abs().sum() * 100),
                "mae": float(error.abs().mean()),
                "rmse": float(np.sqrt(np.square(error).mean())),
                "signed_bias": float(error.mean()),
                "p10_p90_coverage": float(group.actual.between(group.p10, group.p90).mean()),
                "mean_interval_width": float((group.p90 - group.p10).mean()),
                "mean_pinball_loss": float(np.mean(pinball)),
            })
    return pd.DataFrame(rows)


def assert_operational_safety(frame: pd.DataFrame, final_test_start: pd.Timestamp) -> None:
    if frame.oracle_weather.fillna(False).any():
        raise AssertionError("V1 operational predictions contain oracle weather")
    if (pd.to_datetime(frame.target_month) < final_test_start).any():
        raise AssertionError("final evaluation contains development observations")
    expected = pd.to_datetime(frame.origin).dt.to_period("M") + frame.horizon.to_numpy()
    if not (expected == pd.to_datetime(frame.target_month).dt.to_period("M")).all():
        raise AssertionError("V1 target/horizon alignment failed")
    if not (frame.p10 <= frame.p50).all() or not (frame.p50 <= frame.p90).all():
        raise AssertionError("V1 calibrated quantiles cross")
    if {"weather_available_at", "operational_forecast_time"}.issubset(frame):
        weather_rows = frame.weather_available_at.notna()
        if (pd.to_datetime(frame.loc[weather_rows, "weather_available_at"], utc=True)
                > pd.to_datetime(frame.loc[weather_rows, "operational_forecast_time"], utc=True)).any():
            raise AssertionError("V1 weather becomes available after forecast issuance")
        providers = set(frame.loc[weather_rows, "weather_provider"].dropna())
        if providers - {"era5_expanding_normal"}:
            raise AssertionError(f"unexpected V1 operational weather provider: {sorted(providers)}")
