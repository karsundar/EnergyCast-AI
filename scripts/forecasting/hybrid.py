"""Leakage-safe Phase 4B statistical and residual-correction helpers."""

from __future__ import annotations

from typing import Any, Iterable

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from statsmodels.tsa.statespace.sarimax import SARIMAX

from .baselines import ZONE_CODES
from .weather_oracle import (
    MONTHLY_WEATHER_FEATURES, holt_winters_forecast, month_calendar,
    sarimax_forecast,
)


def sarima_forecast(history: pd.Series, steps: int, config: dict[str, Any]) -> np.ndarray:
    """SARIMA control with the same family/order as SARIMAX but no exogenous weather."""
    model = SARIMAX(
        history.astype(float), order=tuple(config["order"]),
        seasonal_order=tuple(config["seasonal_order"]),
        enforce_stationarity=False, enforce_invertibility=False,
    ).fit(disp=False, maxiter=int(config.get("maxiter", 100)))
    return np.asarray(model.forecast(steps), dtype=float)


def residual_features(frame: pd.DataFrame, geography: str, target: str,
                      origin: pd.Timestamp, target_month: pd.Timestamp,
                      horizon: int, baseline: float, include_weather: bool) -> dict[str, float]:
    indexed = frame[frame["geography"] == geography].set_index("month").sort_index()
    history = indexed[target].loc[:origin]
    origin_value = float(history.loc[origin])
    row = {
        "geography_code": float(ZONE_CODES[geography]), "horizon": float(horizon),
        "baseline": float(baseline), "origin_value": origin_value,
        "baseline_growth": float(baseline / origin_value - 1) if origin_value else 0.0,
        "origin_mom_growth": float(origin_value / history.get(origin - pd.DateOffset(months=1), np.nan) - 1),
        "origin_yoy_growth": float(origin_value / history.get(origin - pd.DateOffset(months=12), np.nan) - 1),
    }
    row.update(month_calendar(target_month))
    if include_weather:
        weather = indexed.loc[target_month]
        row.update({f"oracle_{column}": float(weather[column]) for column in MONTHLY_WEATHER_FEATURES})
    return row


def historical_oos_residuals(frame: pd.DataFrame, target: str, baseline_model: str,
                             origins: Iterable[pd.Timestamp], horizon: int,
                             config: dict[str, Any]) -> pd.DataFrame:
    """Generate residual targets strictly from forecasts made at earlier origins."""
    if baseline_model not in {"holt_winters", "sarima", "sarimax_oracle"}:
        raise ValueError(f"unsupported residual baseline: {baseline_model}")
    include_weather = baseline_model == "sarimax_oracle"
    weather_index = frame.set_index(["geography", "month"])
    records: list[dict[str, Any]] = []
    for origin in map(pd.Timestamp, origins):
        for geography, group in frame.groupby("geography", observed=True):
            series = group.set_index("month")[target].sort_index()
            history = series.loc[:origin].dropna()
            months = pd.date_range(origin + pd.DateOffset(months=1), periods=horizon, freq="MS")
            if baseline_model == "holt_winters":
                values = holt_winters_forecast(history, horizon)
            elif baseline_model == "sarimax_oracle":
                history_weather = weather_index.loc[geography].loc[history.index, list(MONTHLY_WEATHER_FEATURES)]
                future_weather = weather_index.loc[geography].loc[months, list(MONTHLY_WEATHER_FEATURES)]
                values = sarimax_forecast(history, history_weather, future_weather, config["sarimax"])
            else:
                values = sarima_forecast(history, horizon, config["sarimax"])
            for step, (month, prediction) in enumerate(zip(months, values, strict=True), 1):
                actual = float(series.get(month, np.nan))
                if not np.isfinite(actual):
                    continue
                features = residual_features(frame, geography, target, origin, month, step, float(prediction), include_weather)
                records.append({
                    **features, "origin": origin, "target_month": month, "geography": geography,
                    "actual": actual, "baseline_prediction": float(prediction),
                    "level_residual": actual - float(prediction),
                    "growth_residual": (actual - float(prediction)) / max(abs(float(prediction)), 1e-9),
                })
    return pd.DataFrame.from_records(records)


def assert_residual_samples_leakage_safe(samples: pd.DataFrame) -> None:
    """Reject residual samples that expose outcomes unavailable at forecast origin."""
    required = {"origin", "target_month", "actual", "baseline_prediction"}
    missing = required - set(samples)
    if missing:
        raise ValueError(f"residual samples missing required columns: {sorted(missing)}")
    if samples.empty or not (samples["target_month"] > samples["origin"]).all():
        raise ValueError("every residual target must occur strictly after its forecast origin")
    forbidden = {"actual", "level_residual", "growth_residual"} & set(residual_columns(samples))
    if forbidden:
        raise ValueError(f"future outcomes leaked into residual features: {sorted(forbidden)}")


def validation_residual_calibration(samples: pd.DataFrame, params: dict[str, Any],
                                    transform: str, selection_end: str = "2020-12-01") -> dict[int, tuple[float, float]]:
    """Fit on pre-2018 data and derive P10/P90 offsets only from development residuals."""
    assert_residual_samples_leakage_safe(samples)
    train = samples[samples["target_month"] <= pd.Timestamp("2017-12-01")].dropna()
    validation = samples[
        samples["target_month"].between(pd.Timestamp("2018-01-01"), pd.Timestamp(selection_end))
    ].dropna()
    if train.empty or validation.empty:
        raise ValueError("insufficient samples for validation-only interval calibration")
    model = fit_residual_model(train, transform, params)
    corrected = apply_residual_model(
        model, validation[residual_columns(samples)],
        validation["baseline_prediction"].to_numpy(), transform,
    )
    errors = validation.assign(calibration_error=validation["actual"].to_numpy() - corrected)
    return {
        int(horizon): (float(group["calibration_error"].quantile(.1)),
                       float(group["calibration_error"].quantile(.9)))
        for horizon, group in errors.groupby("horizon", observed=True)
    }


def residual_columns(samples: pd.DataFrame) -> list[str]:
    excluded = {
        "origin", "target_month", "geography", "actual", "baseline_prediction",
        "level_residual", "growth_residual",
    }
    return [column for column in samples if column not in excluded]


def select_residual_transform(samples: pd.DataFrame, params: dict[str, Any],
                              selection_end: str = "2020-12-01") -> tuple[str, dict[str, float]]:
    """Select level versus proportional residual correction on validation data only."""
    train = samples[samples["target_month"] <= pd.Timestamp("2017-12-01")].dropna()
    validation = samples[
        samples["target_month"].between(pd.Timestamp("2018-01-01"), pd.Timestamp(selection_end))
    ].dropna()
    if train.empty or validation.empty:
        raise ValueError("insufficient chronological residual samples for transform selection")
    scores: dict[str, float] = {}
    for transform in ("level", "growth"):
        label = f"{transform}_residual"
        model = LGBMRegressor(objective="regression_l1", verbosity=-1, **params)
        model.fit(train[residual_columns(samples)], train[label])
        correction = model.predict(validation[residual_columns(samples)])
        prediction = validation["baseline_prediction"].to_numpy() + correction
        if transform == "growth":
            prediction = validation["baseline_prediction"].to_numpy() * (1 + correction)
        scores[transform] = float(np.abs(prediction - validation["actual"]).sum() / validation["actual"].abs().sum() * 100)
    return min(scores, key=scores.get), scores


def fit_residual_model(samples: pd.DataFrame, transform: str,
                       params: dict[str, Any]) -> LGBMRegressor:
    model = LGBMRegressor(objective="regression_l1", verbosity=-1, **params)
    model.fit(samples[residual_columns(samples)], samples[f"{transform}_residual"])
    return model


def apply_residual_model(model: LGBMRegressor, features: pd.DataFrame,
                         baselines: np.ndarray, transform: str) -> np.ndarray:
    correction = model.predict(features[residual_columns(features)])
    return baselines + correction if transform == "level" else baselines * (1 + correction)
