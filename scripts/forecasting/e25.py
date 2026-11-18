"""Leakage-safe E25 Holt-Winters residual correction helpers."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from .hybrid import residual_features
from .operational_weather import WEATHER_FEATURES


def residual_available_at(target_month: pd.Series, lag_days: int = 1) -> pd.Series:
    """Monthly residual label is available only after its target month closes."""
    return pd.to_datetime(target_month) + pd.offsets.MonthEnd(0) + pd.Timedelta(days=lag_days)


def attach_expanding_normals(
    samples: pd.DataFrame, historical: pd.DataFrame, minimum_years: int,
) -> pd.DataFrame:
    """Attach same-calendar-month weather normals observable at each OOS origin."""
    history = historical.copy()
    history["target_month"] = pd.to_datetime(history.target_month).dt.to_period("M").dt.to_timestamp()
    history["geography"] = history.weather_zone.replace({"SYSTEM": "ERCOT_SYSTEM"})
    records = []
    for row in samples.itertuples(index=False):
        eligible = history[
            history.geography.eq(row.geography)
            & history.target_month.dt.month.eq(row.target_month.month)
            & history.target_month.le(row.origin)
        ]
        values = eligible[list(WEATHER_FEATURES)].mean() if len(eligible) >= minimum_years else None
        records.append({f"weather_{name}": (float(values[name]) if values is not None else np.nan)
                        for name in WEATHER_FEATURES})
    return pd.concat([samples.reset_index(drop=True), pd.DataFrame(records)], axis=1)


def e25_feature_columns(samples: pd.DataFrame) -> list[str]:
    columns = [
        "geography_code", "horizon", "baseline", "origin_value", "baseline_growth",
        "origin_mom_growth", "origin_yoy_growth", "month_number", "quarter",
        "month_sin", "month_cos", "days_in_month",
        *[f"weather_{name}" for name in WEATHER_FEATURES],
    ]
    missing = set(columns) - set(samples)
    if missing:
        raise ValueError(f"E25 features missing: {sorted(missing)}")
    return columns


def select_lambda(
    samples: pd.DataFrame, parameters: dict[str, Any], lambda_grid: list[float],
    training_end: pd.Timestamp, validation_start: pd.Timestamp,
    validation_end: pd.Timestamp,
) -> tuple[float, dict[str, float]]:
    """Select shrinkage on development validation only, including no correction."""
    columns = e25_feature_columns(samples)
    train = samples[samples.target_month.le(training_end)].dropna(subset=columns + ["level_residual"])
    validation = samples[samples.target_month.between(validation_start, validation_end)].dropna(
        subset=columns + ["level_residual"]
    )
    if train.empty or validation.empty:
        raise ValueError("insufficient chronological residual development samples")
    model = LGBMRegressor(objective="regression_l1", verbosity=-1, **parameters)
    model.fit(train[columns], train.level_residual)
    correction = model.predict(validation[columns])
    scores = {}
    for shrinkage in lambda_grid:
        prediction = validation.baseline_prediction.to_numpy() + float(shrinkage) * correction
        scores[str(shrinkage)] = float(
            np.abs(prediction - validation.actual).sum() / validation.actual.abs().sum() * 100
        )
    selected = min(lambda_grid, key=lambda value: scores[str(value)])
    return float(selected), scores


def fit_residual_model_at(
    samples: pd.DataFrame, issuance_time: pd.Timestamp, parameters: dict[str, Any],
) -> tuple[LGBMRegressor, pd.Timestamp]:
    columns = e25_feature_columns(samples)
    cutoff = pd.Timestamp(issuance_time).tz_localize(None)
    eligible = samples[pd.to_datetime(samples.residual_available_at).le(cutoff)].dropna(
        subset=columns + ["level_residual"]
    )
    if eligible.empty or pd.to_datetime(eligible.residual_available_at).max() > cutoff:
        raise ValueError("residual labels cross issuance time")
    model = LGBMRegressor(objective="regression_l1", verbosity=-1, **parameters)
    model.fit(eligible[columns], eligible.level_residual)
    return model, pd.Timestamp(eligible.residual_available_at.max())


def query_features(
    demand: pd.DataFrame, weather_queries: pd.DataFrame, baselines: pd.DataFrame,
    target: str, origin: pd.Timestamp,
) -> pd.DataFrame:
    records = []
    indexed = baselines.set_index(["geography", "target_month", "horizon"])
    for row in weather_queries.itertuples(index=False):
        baseline = indexed.loc[(row.geography, row.target_month, row.horizon)]
        feature = residual_features(
            demand, row.geography, target, origin, row.target_month,
            int(row.horizon), float(baseline.p50), False,
        )
        for name in WEATHER_FEATURES:
            feature[f"weather_{name}"] = float(getattr(row, f"weather_{name}"))
        feature.update({
            "origin": origin, "target_month": row.target_month, "horizon": int(row.horizon),
            "geography": row.geography, "baseline_prediction": float(baseline.p50),
            "actual": float(baseline.actual), "weather_provider": row.weather_provider,
            "weather_available_at": row.weather_available_at,
            "fallback_reason": row.fallback_reason,
            "training_max": float(
                demand[demand.geography.eq(row.geography)].set_index("month")[target].loc[:origin].max()
            ),
        })
        records.append(feature)
    return pd.DataFrame(records)


def assert_e25_leakage_safe(predictions: pd.DataFrame) -> None:
    if predictions.oracle_weather.any():
        raise AssertionError("E25 contains oracle weather")
    if not predictions.experiment.isin(["E25-A", "E25-B", "E25-C"]).all():
        raise AssertionError("unexpected E25 experiment")
    if (pd.to_datetime(predictions.residual_training_cutoff, utc=True)
            > pd.to_datetime(predictions.operational_forecast_time, utc=True)).any():
        raise AssertionError("residual target unavailable at issuance")
    expected = predictions.origin.dt.to_period("M") + predictions.horizon.to_numpy()
    if not (expected == predictions.target_month.dt.to_period("M")).all():
        raise AssertionError("target/horizon alignment failed")
    if not predictions.shrinkage_lambda.between(0, 1).all():
        raise AssertionError("invalid shrinkage")
