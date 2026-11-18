"""Leakage-safe E24 multiplicative demand forecasting helpers."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from .e25 import e25_feature_columns, residual_available_at
from .hybrid import residual_features
from .operational_weather import WEATHER_FEATURES


def build_log_ratio_samples(
    oos_holt_winters: pd.DataFrame,
    demand: pd.DataFrame,
    weather_samples: pd.DataFrame,
    reference: str,
    publication_lag_days: int = 1,
) -> pd.DataFrame:
    """Build labels relative to an OOS HW or observable seasonal reference."""
    if reference not in {"holt_winters_oos", "seasonal_naive"}:
        raise ValueError(f"unsupported E24 reference: {reference}")
    frame = oos_holt_winters.copy()
    frame["origin"] = pd.to_datetime(frame.origin)
    frame["target_month"] = pd.to_datetime(frame.target_month)
    if reference == "holt_winters_oos":
        frame["reference_value"] = frame.baseline_prediction.astype(float)
        frame["reference_month"] = frame.origin
    else:
        lookup = demand.set_index(["geography", "month"])
        reference_months = frame.target_month - pd.DateOffset(months=12)
        frame["reference_month"] = reference_months
        frame["reference_value"] = [
            lookup.loc[(geography, month), "value"]
            for geography, month in zip(frame.geography, reference_months, strict=True)
        ]
    if (frame.reference_month > frame.origin).any():
        raise ValueError("seasonal reference is unavailable at forecast origin")
    if (frame[["actual", "reference_value"]] <= 0).any().any():
        raise ValueError("E24 log-ratio target and denominator must be strictly positive")
    frame["baseline_prediction"] = frame.reference_value
    frame["baseline"] = frame.reference_value
    frame["baseline_growth"] = frame.reference_value / frame.origin_value - 1
    frame["log_ratio"] = np.log(frame.actual / frame.reference_value)
    frame["label_available_at"] = residual_available_at(frame.target_month, publication_lag_days)
    keys = ["origin", "target_month", "horizon", "geography"]
    weather = weather_samples[keys + [f"weather_{name}" for name in WEATHER_FEATURES]]
    frame = frame.drop(columns=[column for column in weather if column in frame and column not in keys])
    frame = frame.merge(weather, on=keys, how="left", validate="one_to_one")
    e24_feature_columns(frame)
    return frame


def e24_feature_columns(samples: pd.DataFrame) -> list[str]:
    """Use the exact E25 demand/calendar/weather feature schema."""
    return e25_feature_columns(samples)


def select_multiplicative_lambda(
    samples: pd.DataFrame,
    parameters: dict[str, Any],
    lambda_grid: list[float],
    training_end: pd.Timestamp,
    validation_start: pd.Timestamp,
    validation_end: pd.Timestamp,
) -> tuple[float, dict[str, float]]:
    columns = e24_feature_columns(samples)
    required = columns + ["log_ratio", "actual", "reference_value"]
    train = samples[samples.target_month.le(training_end)].dropna(subset=required)
    validation = samples[samples.target_month.between(validation_start, validation_end)].dropna(subset=required)
    if train.empty or validation.empty:
        raise ValueError("insufficient chronological E24 development samples")
    model = LGBMRegressor(objective="regression_l1", verbosity=-1, **parameters)
    model.fit(train[columns], train.log_ratio)
    ratio = model.predict(validation[columns])
    scores = {}
    for shrinkage in lambda_grid:
        prediction = validation.reference_value.to_numpy() * np.exp(float(shrinkage) * ratio)
        scores[str(shrinkage)] = float(
            np.abs(prediction - validation.actual).sum() / validation.actual.abs().sum() * 100
        )
    selected = min(lambda_grid, key=lambda value: scores[str(value)])
    return float(selected), scores


def fit_log_ratio_model_at(
    samples: pd.DataFrame, issuance_time: pd.Timestamp, parameters: dict[str, Any],
) -> tuple[LGBMRegressor, pd.Timestamp]:
    columns = e24_feature_columns(samples)
    cutoff = pd.Timestamp(issuance_time).tz_localize(None)
    eligible = samples[pd.to_datetime(samples.label_available_at).le(cutoff)].dropna(
        subset=columns + ["log_ratio"]
    )
    if eligible.empty or pd.to_datetime(eligible.label_available_at).max() > cutoff:
        raise ValueError("E24 target labels cross issuance time")
    model = LGBMRegressor(objective="regression_l1", verbosity=-1, **parameters)
    model.fit(eligible[columns], eligible.log_ratio)
    return model, pd.Timestamp(eligible.label_available_at.max())


def query_features(
    demand: pd.DataFrame,
    weather_queries: pd.DataFrame,
    hw_baselines: pd.DataFrame,
    target: str,
    origin: pd.Timestamp,
    reference: str,
) -> pd.DataFrame:
    """Construct queries using only origin-observable demand and climate normals."""
    hw = hw_baselines.set_index(["geography", "target_month", "horizon"])
    demand_index = demand.set_index(["geography", "month"])
    records = []
    for row in weather_queries.itertuples(index=False):
        evaluation = hw.loc[(row.geography, row.target_month, row.horizon)]
        if reference == "holt_winters_oos":
            baseline = float(evaluation.p50)
            reference_month = origin
        else:
            reference_month = row.target_month - pd.DateOffset(months=12)
            if reference_month > origin:
                raise ValueError("query seasonal reference is unavailable at origin")
            baseline = float(demand_index.loc[(row.geography, reference_month), target])
        if baseline <= 0:
            raise ValueError("query denominator must be strictly positive")
        feature = residual_features(
            demand, row.geography, target, origin, row.target_month,
            int(row.horizon), baseline, False,
        )
        for name in WEATHER_FEATURES:
            feature[f"weather_{name}"] = float(getattr(row, f"weather_{name}"))
        history = demand[demand.geography.eq(row.geography)].set_index("month")[target].loc[:origin]
        feature.update({
            "origin": origin, "target_month": row.target_month, "horizon": int(row.horizon),
            "geography": row.geography, "reference_value": baseline,
            "reference_month": reference_month, "actual": float(evaluation.actual),
            "weather_provider": row.weather_provider,
            "weather_available_at": row.weather_available_at,
            "fallback_reason": row.fallback_reason, "training_max": float(history.max()),
        })
        records.append(feature)
    return pd.DataFrame(records)


def reconstruct(reference: np.ndarray, predicted_log_ratio: np.ndarray, shrinkage: float) -> np.ndarray:
    if np.any(np.asarray(reference) <= 0) or not 0 <= shrinkage <= 1:
        raise ValueError("invalid multiplicative reconstruction")
    return np.asarray(reference) * np.exp(float(shrinkage) * np.asarray(predicted_log_ratio))


def assert_e24_leakage_safe(predictions: pd.DataFrame) -> None:
    if predictions.oracle_weather.any():
        raise AssertionError("E24 contains oracle weather")
    if not predictions.experiment.isin(["E24-A", "E24-B", "E24-C"]).all():
        raise AssertionError("unexpected E24 experiment")
    if (predictions.reference_value <= 0).any() or (predictions.p50 <= 0).any():
        raise AssertionError("nonpositive multiplicative value")
    if (pd.to_datetime(predictions.reference_month) > pd.to_datetime(predictions.origin)).any():
        raise AssertionError("seasonal reference crosses forecast origin")
    if (pd.to_datetime(predictions.label_training_cutoff, utc=True)
            > pd.to_datetime(predictions.operational_forecast_time, utc=True)).any():
        raise AssertionError("E24 label unavailable at issuance")
    if (pd.to_datetime(predictions.weather_available_at, utc=True)
            > pd.to_datetime(predictions.operational_forecast_time, utc=True)).any():
        raise AssertionError("weather unavailable at issuance")
    expected = predictions.origin.dt.to_period("M") + predictions.horizon.to_numpy()
    if not (expected == predictions.target_month.dt.to_period("M")).all():
        raise AssertionError("target/horizon alignment failed")
    if not predictions.shrinkage_lambda.between(0, 1).all():
        raise AssertionError("invalid shrinkage")
