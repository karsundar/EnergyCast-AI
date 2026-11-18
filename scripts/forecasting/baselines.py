"""Statistical and LightGBM baseline models for monthly ERCOT forecasts."""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from .features import LAGS, SEASONS, WINDOWS


MODEL_NAMES = ("seasonal_naive", "drift_seasonal_naive", "holt_winters", "lightgbm_direct")
ZONE_CODES = {
    "COAST": 0, "EAST": 1, "FAR_WEST": 2, "NORTH": 3, "NORTH_CENTRAL": 4,
    "SOUTHERN": 5, "SOUTH_CENTRAL": 6, "WEST": 7, "ERCOT_SYSTEM": 8,
}
LGBM_FEATURES = [
    "horizon", "geography_code", "future_month", "future_quarter", "future_days_in_month",
    "future_weekday_count", "future_weekend_count", "future_holiday_count", "future_month_sin",
    "future_month_cos", "future_trend", "origin_value", *[f"origin_lag_{lag}" for lag in LAGS],
    *[f"origin_trailing_mean_{window}" for window in WINDOWS], "origin_mom_growth",
    "origin_yoy_growth", "origin_peak_demand",
]


def seasonal_naive(history: pd.Series, target_months: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray([history.get(month - pd.DateOffset(years=1), np.nan) for month in target_months], dtype=float)


def drift_seasonal_naive(history: pd.Series, target_months: pd.DatetimeIndex) -> np.ndarray:
    base = seasonal_naive(history, target_months)
    if len(history.dropna()) < 13:
        return base
    origin = history.index.max()
    current = history.get(origin, np.nan)
    prior_year = history.get(origin - pd.DateOffset(years=1), np.nan)
    annual_change = current - prior_year
    if not np.isfinite(annual_change):
        return base
    horizons = np.arange(1, len(target_months) + 1, dtype=float)
    return base + annual_change * horizons / 12.0


def holt_winters(history: pd.Series, steps: int) -> np.ndarray:
    clean_tail = history.copy()
    if clean_tail.isna().any():
        last_missing = np.flatnonzero(clean_tail.isna().to_numpy())
        clean_tail = clean_tail.iloc[last_missing[-1] + 1:]
    if len(clean_tail) < 24:
        return np.full(steps, np.nan)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fitted = ExponentialSmoothing(
                clean_tail.astype(float), trend="add", seasonal="add", seasonal_periods=12,
                initialization_method="estimated",
            ).fit(optimized=True, remove_bias=True)
        return np.asarray(fitted.forecast(steps), dtype=float)
    except (ValueError, RuntimeError, np.linalg.LinAlgError):
        return np.full(steps, np.nan)


def fit_lightgbm(samples: pd.DataFrame, params: dict[str, Any]) -> LGBMRegressor:
    model = LGBMRegressor(objective="regression_l1", verbosity=-1, n_jobs=4, random_state=42, **params)
    model.fit(samples[LGBM_FEATURES], samples["label"])
    return model


def future_calendar(month: pd.Timestamp) -> dict[str, float]:
    start = pd.Timestamp(month)
    dates = pd.date_range(start, start + pd.offsets.MonthEnd(1), freq="D")
    # The holiday count is inserted by the caller from the feature store; this
    # fallback remains deterministic for tests and non-holiday use.
    return {
        "future_month": float(start.month),
        "future_quarter": float(start.quarter),
        "future_days_in_month": float(start.days_in_month),
        "future_weekday_count": float((dates.day_of_week < 5).sum()),
        "future_weekend_count": float((dates.day_of_week >= 5).sum()),
        "future_month_sin": float(np.sin(2 * np.pi * start.month / 12)),
        "future_month_cos": float(np.cos(2 * np.pi * start.month / 12)),
    }
