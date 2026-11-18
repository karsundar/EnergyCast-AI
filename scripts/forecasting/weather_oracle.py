"""Leakage-safe Phase 4A oracle-weather feature and model helpers."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX

from .baselines import ZONE_CODES


TARGETS = ("total_energy_mwh", "peak_demand_mw")
WEATHER_FEATURES = (
    "temperature_mean_c", "temperature_min_c", "temperature_max_c",
    "relative_humidity_pct", "wind_speed_mps", "precipitation_mm",
    "solar_energy_mj_m2", "hdd_65f", "cdd_65f",
)
MONTHLY_WEATHER_FEATURES = WEATHER_FEATURES + (
    "extreme_heat_days", "freezing_days", "heatwave_days", "coldwave_days",
)
DAILY_WEATHER_FEATURES = WEATHER_FEATURES + (
    "extreme_heat_day", "freezing_day", "heatwave_3day", "coldwave_3day",
)
MONTH_LAGS = (1, 2, 3, 6, 12, 24)
DAY_LAGS = (1, 2, 7, 14, 28, 365)


@dataclass(frozen=True)
class SplitConfig:
    training_end: str = "2017-12-01"
    validation_start: str = "2018-01-01"
    validation_end: str = "2020-12-01"
    test_start: str = "2021-01-01"


def canonical_zone(value: str) -> str:
    return "ERCOT_SYSTEM" if value == "SYSTEM" else str(value)


def prepare_monthly(monthly_weather: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """Join validated weather to canonical demand targets without changing targets."""
    weather = monthly_weather.copy()
    weather["geography"] = weather["weather_zone"].map(canonical_zone)
    weather["month"] = pd.to_datetime(weather["month"], utc=True).dt.tz_convert("America/Chicago").dt.tz_localize(None).dt.to_period("M").dt.to_timestamp()
    target = targets.copy()
    target["month"] = pd.to_datetime(target["month"]).dt.to_period("M").dt.to_timestamp()
    keep = ["month", "geography", *MONTHLY_WEATHER_FEATURES, "mapping_approximate"]
    result = target.merge(weather[keep], on=["month", "geography"], how="inner", validate="one_to_one")
    return result.sort_values(["geography", "month"]).reset_index(drop=True)


def prepare_daily(daily_weather: pd.DataFrame, threshold: float = 99.0) -> pd.DataFrame:
    frame = daily_weather.copy()
    frame["geography"] = frame["weather_zone"].map(canonical_zone)
    frame["date"] = pd.to_datetime(frame["date"])
    local_midnight = frame["date"].dt.tz_localize("America/Chicago", ambiguous="infer", nonexistent="shift_forward")
    next_midnight = (frame["date"] + pd.Timedelta(days=1)).dt.tz_localize(
        "America/Chicago", ambiguous="infer", nonexistent="shift_forward"
    )
    frame["expected_observation_count"] = ((next_midnight - local_midnight).dt.total_seconds() / 3600).astype(int)
    frame["demand_completeness_pct"] = frame["observation_count"] / frame["expected_observation_count"] * 100
    frame["target_valid"] = (
        frame["completeness_pct"].ge(threshold)
        & frame["demand_completeness_pct"].ge(threshold)
        & frame[list(TARGETS)].notna().all(axis=1)
    )
    return frame.sort_values(["geography", "date"]).reset_index(drop=True)


def month_calendar(month: pd.Timestamp) -> dict[str, float]:
    month = pd.Timestamp(month)
    return {
        "month_number": float(month.month), "quarter": float(month.quarter),
        "month_sin": float(np.sin(2 * np.pi * month.month / 12)),
        "month_cos": float(np.cos(2 * np.pi * month.month / 12)),
        "days_in_month": float(month.days_in_month),
    }


def monthly_direct_samples(frame: pd.DataFrame, target: str, horizon: int = 12,
                           include_weather: bool = False) -> pd.DataFrame:
    """Direct multi-horizon rows; every demand feature is frozen at origin."""
    records: list[dict[str, Any]] = []
    weather_lookup = frame.set_index(["geography", "month"])
    first_month = frame["month"].min()
    for geography, group in frame.groupby("geography", observed=True):
        group = group.set_index("month").sort_index()
        series = group[target].where(group["target_valid"])
        peaks = group["peak_demand_mw"].where(group["target_valid"])
        for origin in series.index[24:-1]:
            if not np.isfinite(series.get(origin, np.nan)):
                continue
            history = series.loc[:origin]
            for step in range(1, horizon + 1):
                target_month = origin + pd.DateOffset(months=step)
                label = series.get(target_month, np.nan)
                if not np.isfinite(label):
                    continue
                row: dict[str, Any] = {
                    "origin": origin, "target_month": target_month, "horizon": step,
                    "geography": geography, "geography_code": ZONE_CODES[geography],
                    "trend": (target_month.year - first_month.year) * 12 + target_month.month - first_month.month,
                    "origin_value": history.get(origin), "origin_peak": peaks.get(origin), "label": label,
                }
                row.update(month_calendar(target_month))
                for lag in MONTH_LAGS:
                    row[f"origin_lag_{lag}"] = history.get(origin - pd.DateOffset(months=lag), np.nan)
                for window in (3, 6, 12, 24):
                    values = history.tail(window)
                    row[f"origin_mean_{window}"] = values.mean() if len(values) == window and values.notna().all() else np.nan
                if include_weather:
                    weather = weather_lookup.loc[(geography, target_month)]
                    for column in MONTHLY_WEATHER_FEATURES:
                        row[f"oracle_{column}"] = float(weather[column])
                records.append(row)
    return pd.DataFrame.from_records(records)


def model_columns(samples: pd.DataFrame) -> list[str]:
    excluded = {"origin", "target_month", "geography", "label"}
    return [column for column in samples.columns if column not in excluded]


def fit_lgbm(samples: pd.DataFrame, params: dict[str, Any]) -> LGBMRegressor:
    model = LGBMRegressor(objective="regression_l1", verbosity=-1, **params)
    model.fit(samples[model_columns(samples)], samples["label"])
    return model


def holt_winters_forecast(history: pd.Series, steps: int) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fitted = ExponentialSmoothing(history.astype(float), trend="add", seasonal="add", seasonal_periods=12).fit(remove_bias=True)
    return np.asarray(fitted.forecast(steps), dtype=float)


def sarimax_forecast(history: pd.Series, history_weather: pd.DataFrame,
                     future_weather: pd.DataFrame, config: dict[str, Any]) -> np.ndarray:
    """Fit only through origin; future exogenous values are observed-weather oracle inputs."""
    valid = history.notna() & history_weather.notna().all(axis=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = SARIMAX(
            history.loc[valid].astype(float), exog=history_weather.loc[valid].astype(float),
            order=tuple(config["order"]), seasonal_order=tuple(config["seasonal_order"]),
            enforce_stationarity=False, enforce_invertibility=False,
        ).fit(disp=False, maxiter=int(config.get("maxiter", 100)))
    return np.asarray(model.forecast(len(future_weather), exog=future_weather.astype(float)), dtype=float)


def monthly_predictions(frame: pd.DataFrame, target: str, origins: Iterable[pd.Timestamp],
                        experiment: str, config: dict[str, Any], max_horizon: int = 12,
                        samples: pd.DataFrame | None = None) -> pd.DataFrame:
    include_weather = experiment == "E3"
    if samples is None:
        samples = monthly_direct_samples(frame, target, max_horizon, include_weather)
    weather_index = frame.set_index(["geography", "month"])
    records: list[dict[str, Any]] = []
    for origin in map(pd.Timestamp, origins):
        training = samples[samples["target_month"] <= origin]
        lgbm = fit_lgbm(training, config["lightgbm"]) if experiment in {"E2", "E3"} else None
        for geography, group in frame.groupby("geography", observed=True):
            series = group.set_index("month")[target].sort_index()
            history = series.loc[:origin].dropna()
            months = pd.date_range(origin + pd.DateOffset(months=1), periods=max_horizon, freq="MS")
            if experiment == "E0":
                values = holt_winters_forecast(history, max_horizon)
            elif experiment == "E1":
                hist_weather = weather_index.loc[geography].loc[history.index, list(MONTHLY_WEATHER_FEATURES)]
                future_weather = weather_index.loc[geography].loc[months, list(MONTHLY_WEATHER_FEATURES)]
                values = sarimax_forecast(history, hist_weather, future_weather, config["sarimax"])
            else:
                query = samples[(samples["origin"] == origin) & (samples["geography"] == geography)]
                query = query.set_index("target_month").reindex(months)
                values = lgbm.predict(query[model_columns(training)])
            for step, (month, value) in enumerate(zip(months, values, strict=True), 1):
                actual = series.get(month, np.nan)
                records.append({
                    "experiment": experiment, "model": {"E0": "holt_winters", "E1": "sarimax_oracle", "E2": "lightgbm_monthly", "E3": "lightgbm_monthly_oracle"}[experiment],
                    "frequency": "monthly", "forecast_path": "direct_monthly", "target_name": target,
                    "origin": origin, "target_month": month, "horizon": step, "geography": geography,
                    "p50": float(value), "actual": float(actual) if np.isfinite(actual) else np.nan,
                    "oracle_weather": experiment in {"E1", "E3"},
                    "forecast_status": "complete" if np.isfinite(actual) else "incomplete_horizon",
                })
    return pd.DataFrame.from_records(records)


def add_daily_lags(frame: pd.DataFrame, target: str) -> pd.DataFrame:
    output = frame.copy()
    grouped = output.groupby("geography", observed=True)[target]
    for lag in DAY_LAGS:
        output[f"lag_{lag}"] = grouped.shift(lag)
    for window in (7, 28, 365):
        output[f"mean_{window}"] = grouped.transform(lambda values: values.shift(1).rolling(window).mean())
    output["day_of_year"] = output["date"].dt.dayofyear
    output["day_of_week"] = output["date"].dt.dayofweek
    output["month_number"] = output["date"].dt.month
    output["geography_code"] = output["geography"].map(ZONE_CODES)
    return output


def daily_feature_columns(include_weather: bool) -> list[str]:
    columns = ["geography_code", "day_of_year", "day_of_week", "month_number"]
    columns += [f"lag_{lag}" for lag in DAY_LAGS] + ["mean_7", "mean_28", "mean_365"]
    if include_weather:
        columns += list(DAILY_WEATHER_FEATURES)
    return columns


def daily_forecast_dates(origin: pd.Timestamp, months: int) -> pd.DatetimeIndex:
    """Return complete calendar months after a month-end forecast origin."""
    origin = pd.Timestamp(origin)
    if origin != origin.to_period("M").end_time.normalize():
        raise ValueError("daily forecast origin must be the final calendar day of a month")
    final_date = (origin.to_period("M") + months).end_time.normalize()
    return pd.date_range(origin + pd.Timedelta(days=1), final_date, freq="D")


def recursive_daily_predictions(frame: pd.DataFrame, target: str, origin: pd.Timestamp,
                                experiment: str, config: dict[str, Any], months: int = 12,
                                enriched: pd.DataFrame | None = None) -> pd.DataFrame:
    """Recursive daily forecast: decoder lags contain predictions, never future actual demand."""
    include_weather = experiment == "E5"
    if enriched is None:
        enriched = add_daily_lags(frame, target)
    features = daily_feature_columns(include_weather)
    training = enriched[(enriched["date"] <= origin) & enriched["target_valid"]].dropna(subset=features + [target])
    model = LGBMRegressor(objective="regression_l1", verbosity=-1, **config["lightgbm"])
    model.fit(training[features], training[target])
    future_dates = daily_forecast_dates(origin, months)
    records: list[dict[str, Any]] = []
    canonical_origin = origin.to_period("M").to_timestamp()
    for geography, group in frame.groupby("geography", observed=True):
        actual_lookup = group.set_index("date")
        values = group[group["date"] <= origin].set_index("date")[target].astype(float).to_dict()
        for date in future_dates:
            row: dict[str, Any] = {
                "geography_code": ZONE_CODES[geography], "day_of_year": date.dayofyear,
                "day_of_week": date.dayofweek, "month_number": date.month,
            }
            for lag in DAY_LAGS:
                row[f"lag_{lag}"] = values.get(date - pd.Timedelta(days=lag), np.nan)
            for window in (7, 28, 365):
                prior = [values.get(date - pd.Timedelta(days=offset), np.nan) for offset in range(1, window + 1)]
                row[f"mean_{window}"] = float(np.mean(prior)) if np.isfinite(prior).all() else np.nan
            if include_weather:
                weather = actual_lookup.loc[date]
                for column in DAILY_WEATHER_FEATURES:
                    row[column] = float(weather[column])
            prediction = float(model.predict(pd.DataFrame([row])[features])[0])
            values[date] = prediction
            actual = actual_lookup[target].get(date, np.nan)
            records.append({
                "experiment": experiment, "model": "lightgbm_daily_oracle" if include_weather else "lightgbm_daily",
                "frequency": "daily", "forecast_path": "daily_recursive", "target_name": target,
                "origin": canonical_origin, "target_date": date, "target_month": date.to_period("M").to_timestamp(),
                "geography": geography, "p50": prediction,
                "actual": float(actual) if np.isfinite(actual) else np.nan,
                "oracle_weather": include_weather,
                "forecast_status": "complete" if np.isfinite(actual) else "incomplete_horizon",
            })
    result = pd.DataFrame.from_records(records)
    result["horizon"] = ((result["target_month"].dt.year - canonical_origin.year) * 12 + result["target_month"].dt.month - canonical_origin.month).astype(int)
    return result


def aggregate_daily_predictions(predictions: pd.DataFrame) -> pd.DataFrame:
    keys = ["experiment", "model", "target_name", "origin", "target_month", "horizon", "geography", "oracle_weather"]
    frames = []
    for target, aggregation in (("total_energy_mwh", "sum"), ("peak_demand_mw", "max")):
        subset = predictions[predictions["target_name"] == target]
        grouped_target = subset.groupby(keys, observed=True)[["p50", "actual"]]
        frame = (grouped_target.sum(min_count=1) if aggregation == "sum" else grouped_target.max()).reset_index()
        frame["frequency"] = "monthly"
        frame["forecast_path"] = "aggregated_daily"
        frame["forecast_status"] = np.where(frame["actual"].notna(), "complete", "incomplete_horizon")
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def assert_oracle_leakage_safe(samples: pd.DataFrame) -> None:
    forbidden = {"actual", "future_demand", "target_value"} & set(model_columns(samples))
    if forbidden:
        raise AssertionError(f"future demand columns in model inputs: {sorted(forbidden)}")
    oracle = [column for column in samples if column.startswith("oracle_")]
    if oracle and not set(oracle).issubset({f"oracle_{column}" for column in MONTHLY_WEATHER_FEATURES}):
        raise AssertionError("unexpected oracle feature")
