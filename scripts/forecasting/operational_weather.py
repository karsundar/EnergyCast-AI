"""Leakage-safe direct monthly electricity forecasting with operational weather."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from .baselines import ZONE_CODES
from .weather_oracle import MONTH_LAGS, month_calendar

WEATHER_FEATURES = (
    "temperature_mean_c", "relative_humidity_pct", "wind_speed_mps",
    "precipitation_mm", "solar_energy_mj_m2",
)
TARGETS = ("total_energy_mwh", "peak_demand_mw")
EXPERIMENT_MODES = {
    "E18": "expanding_climate_normal",
    "E19": "raw_seas5_with_normal_fallback",
    "E20": "raw_ncep_with_normal_fallback",
}


def feature_row(
    series: pd.Series, peaks: pd.Series, origin: pd.Timestamp, target_month: pd.Timestamp,
    geography: str, horizon: int, first_month: pd.Timestamp,
) -> dict[str, float | int | pd.Timestamp | str]:
    """Freeze every demand-derived value at the forecast origin."""
    history = series.loc[:origin]
    row: dict[str, float | int | pd.Timestamp | str] = {
        "origin": origin, "target_month": target_month, "horizon": horizon,
        "geography": geography, "geography_code": ZONE_CODES[geography],
        "trend": (target_month.year - first_month.year) * 12 + target_month.month - first_month.month,
        "origin_value": history.get(origin, np.nan), "origin_peak": peaks.get(origin, np.nan),
    }
    row.update(month_calendar(target_month))
    for lag in MONTH_LAGS:
        row[f"origin_lag_{lag}"] = history.get(origin - pd.DateOffset(months=lag), np.nan)
    for window in (3, 6, 12, 24):
        values = history.tail(window)
        row[f"origin_mean_{window}"] = values.mean() if len(values) == window and values.notna().all() else np.nan
    return row


def historical_samples(
    demand: pd.DataFrame, weather: pd.DataFrame, target: str, maximum_horizon: int,
) -> pd.DataFrame:
    """Supervised rows use weather and labels observed no later than their training cutoff."""
    weather_index = weather.set_index(["geography", "target_month"])
    records: list[dict[str, Any]] = []
    first_month = demand.month.min()
    for geography, group in demand.groupby("geography", observed=True):
        group = group.sort_values("month").set_index("month")
        series = group[target].where(group.target_valid)
        peaks = group.peak_demand_mw.where(group.target_valid)
        for origin in series.index[24:]:
            if not np.isfinite(series.get(origin, np.nan)):
                continue
            for horizon in range(1, maximum_horizon + 1):
                target_month = origin + pd.DateOffset(months=horizon)
                label = series.get(target_month, np.nan)
                key = (geography, target_month)
                if not np.isfinite(label) or key not in weather_index.index:
                    continue
                row = feature_row(series, peaks, origin, target_month, geography, horizon, first_month)
                row["label"] = float(label)
                actual_weather = weather_index.loc[key]
                for variable in WEATHER_FEATURES:
                    row[f"weather_{variable}"] = float(actual_weather[variable])
                records.append(row)
    return pd.DataFrame.from_records(records)


def normal_queries(
    demand: pd.DataFrame, operational: pd.DataFrame, origin: pd.Timestamp, target: str,
    maximum_horizon: int,
) -> pd.DataFrame:
    """Build E18 query rows from expanding normals known at the simulated origin."""
    normal = operational[
        operational.feature_mode.eq("expanding_climate_normal")
        & operational.forecast_lead.le(maximum_horizon)
    ].copy()
    normal["initialization_month"] = (
        pd.to_datetime(normal.weather_initialization, utc=True).dt.tz_localize(None).dt.to_period("M").dt.to_timestamp()
    )
    wanted_initialization = origin + pd.DateOffset(months=1)
    normal = normal[normal.initialization_month.eq(wanted_initialization)]
    normal["geography"] = normal.weather_zone.replace({"SYSTEM": "ERCOT_SYSTEM"})
    index = ["geography", "target_month", "forecast_lead"]
    values = normal.pivot(index=index, columns="feature", values="value").reset_index()
    metadata = normal.groupby(index, observed=True).agg(
        weather_initialization=("weather_initialization", "first"),
        weather_available_at=("availability_timestamp", "max"),
        weather_provider=("provider", "first"),
        fallback_reason=("fallback_reason", "first"),
        availability_evidence=("availability_evidence", "first"),
    ).reset_index()
    values = values.merge(metadata, on=index, validate="one_to_one")
    first_month = demand.month.min()
    records: list[dict[str, Any]] = []
    for geography, group in demand.groupby("geography", observed=True):
        group = group.sort_values("month").set_index("month")
        series = group[target].where(group.target_valid)
        peaks = group.peak_demand_mw.where(group.target_valid)
        for horizon in range(1, maximum_horizon + 1):
            target_month = origin + pd.DateOffset(months=horizon)
            match = values[
                values.geography.eq(geography) & values.target_month.eq(target_month)
                & values.forecast_lead.eq(horizon)
            ]
            if len(match) != 1:
                raise ValueError(f"missing/duplicate E18 weather row: {origin=}, {geography=}, {horizon=}")
            weather_row = match.iloc[0]
            row = feature_row(series, peaks, origin, target_month, geography, horizon, first_month)
            for variable in WEATHER_FEATURES:
                row[f"weather_{variable}"] = float(weather_row[variable])
            for column in ("weather_initialization", "weather_available_at", "weather_provider",
                           "fallback_reason", "availability_evidence"):
                row[column] = weather_row[column]
            row["calibration_training_cutoff"] = pd.NaT
            records.append(row)
    return pd.DataFrame.from_records(records)


def provider_queries(
    demand: pd.DataFrame, operational: pd.DataFrame, seasonal: pd.DataFrame,
    origin: pd.Timestamp, target: str, maximum_horizon: int, provider: str,
) -> pd.DataFrame:
    """Build raw-provider leads 1-6 and expanding-normal fallback leads 7-12."""
    if provider not in {"seas5", "ncep"}:
        raise ValueError(f"unsupported provider: {provider}")
    normals = normal_queries(demand, operational, origin, target, maximum_horizon)
    if maximum_horizon <= 6:
        normal_fallback = normals.iloc[0:0]
    else:
        normal_fallback = normals[normals.horizon.gt(6)].copy()
        normal_fallback["fallback_reason"] = "unsupported_seasonal_lead_7_12"

    raw = seasonal[
        seasonal.track.eq("archived") & seasonal.provider.eq(provider)
        & seasonal.lead_month.le(min(maximum_horizon, 6))
    ].copy()
    raw["initialization_month"] = (
        pd.to_datetime(raw.issue_date, utc=True).dt.tz_localize(None).dt.to_period("M").dt.to_timestamp()
    )
    raw = raw[raw.initialization_month.eq(origin + pd.DateOffset(months=1))]
    raw["geography"] = raw.weather_zone.replace({"SYSTEM": "ERCOT_SYSTEM"})
    raw = raw.set_index(["geography", "target_month", "lead_month"])
    first_month = demand.month.min()
    records: list[dict[str, Any]] = []
    for geography, group in demand.groupby("geography", observed=True):
        group = group.sort_values("month").set_index("month")
        series = group[target].where(group.target_valid)
        peaks = group.peak_demand_mw.where(group.target_valid)
        for horizon in range(1, min(maximum_horizon, 6) + 1):
            target_month = origin + pd.DateOffset(months=horizon)
            key = (geography, target_month, horizon)
            if key not in raw.index:
                raise ValueError(f"missing {provider} weather row: {origin=}, {geography=}, {horizon=}")
            item = raw.loc[key]
            if isinstance(item, pd.DataFrame):
                raise ValueError(f"duplicate {provider} weather row: {origin=}, {geography=}, {horizon=}")
            row = feature_row(series, peaks, origin, target_month, geography, horizon, first_month)
            for variable in WEATHER_FEATURES:
                row[f"weather_{variable}"] = float(item[f"{variable}_raw"])
            row.update({
                "weather_initialization": item.issue_date,
                "weather_available_at": item.available_at,
                "weather_provider": provider,
                "fallback_reason": None,
                "availability_evidence": "scheduled_operational_release_not_file_audit",
                "calibration_training_cutoff": pd.NaT,
            })
            records.append(row)
    dynamic = pd.DataFrame.from_records(records)
    if not normal_fallback.empty:
        normal_fallback["calibration_training_cutoff"] = pd.NaT
    return pd.concat([dynamic, normal_fallback], ignore_index=True, sort=False)


def model_columns(frame: pd.DataFrame) -> list[str]:
    excluded = {
        "origin", "target_month", "geography", "label", "weather_initialization",
        "weather_available_at", "weather_provider", "fallback_reason", "availability_evidence",
        "calibration_training_cutoff",
    }
    return [column for column in frame.columns if column not in excluded]


def fit_operational_model(
    samples: pd.DataFrame, origin: pd.Timestamp, parameters: dict[str, Any],
) -> tuple[LGBMRegressor, pd.Timestamp]:
    training = samples[samples.target_month.le(origin)].dropna(subset=model_columns(samples) + ["label"])
    if training.empty or training.target_month.max() > origin:
        raise ValueError("training labels cross the forecast origin")
    model = LGBMRegressor(objective="regression_l1", verbosity=-1, **parameters)
    model.fit(training[model_columns(training)], training.label)
    return model, pd.Timestamp(training.target_month.max())


def predict_operational(
    model: LGBMRegressor, training_columns: list[str], demand: pd.DataFrame,
    queries: pd.DataFrame, target: str, origin: pd.Timestamp, experiment: str,
    training_label_cutoff: pd.Timestamp,
) -> pd.DataFrame:
    if experiment not in EXPERIMENT_MODES:
        raise ValueError(f"unsupported experiment: {experiment}")
    prediction = model.predict(queries[training_columns])
    actual = demand.set_index(["geography", "month"])[target]
    result = queries[[
        "origin", "target_month", "horizon", "geography", "weather_initialization",
        "weather_available_at", "weather_provider", "fallback_reason", "availability_evidence",
        "calibration_training_cutoff",
    ]].copy()
    result["experiment"] = experiment
    result["model"] = "lightgbm_direct_operational_weather"
    result["frequency"] = "monthly"
    result["forecast_path"] = "direct_monthly"
    result["target_name"] = target
    result["p50"] = prediction
    result["actual"] = [actual.get((row.geography, row.target_month), np.nan) for row in result.itertuples()]
    result["oracle_weather"] = False
    result["weather_mode"] = EXPERIMENT_MODES[experiment]
    result["forecast_status"] = np.where(result.actual.notna(), "complete", "incomplete_horizon")
    result["training_label_cutoff"] = training_label_cutoff
    result["operational_forecast_time"] = pd.to_datetime(result.weather_available_at, utc=True)
    return result


def fit_predict_e18(
    demand: pd.DataFrame, samples: pd.DataFrame, queries: pd.DataFrame, target: str,
    origin: pd.Timestamp, parameters: dict[str, Any],
) -> pd.DataFrame:
    """Backward-compatible E18 wrapper."""
    model, cutoff = fit_operational_model(samples, origin, parameters)
    return predict_operational(
        model, model_columns(samples), demand, queries, target, origin, "E18", cutoff,
    )


def assert_e18_leakage_safe(predictions: pd.DataFrame) -> None:
    if predictions.oracle_weather.any():
        raise AssertionError("E18 is incorrectly labeled oracle")
    if not predictions.weather_mode.eq("expanding_climate_normal").all():
        raise AssertionError("E18 contains dynamic or observed future weather")
    if (predictions.training_label_cutoff > predictions.origin).any():
        raise AssertionError("training labels cross forecast origin")
    expected = predictions.origin.dt.to_period("M") + predictions.horizon.to_numpy()
    if not (expected == predictions.target_month.dt.to_period("M")).all():
        raise AssertionError("target/horizon alignment failed")
    if predictions.horizon.gt(12).any():
        raise AssertionError("unsupported E18 horizon")


def assert_gate_b_leakage_safe(predictions: pd.DataFrame) -> None:
    if predictions.oracle_weather.any():
        raise AssertionError("Gate B contains oracle weather")
    if not predictions.experiment.isin(["E19", "E20"]).all():
        raise AssertionError("unexpected Gate B experiment")
    if (predictions.training_label_cutoff > predictions.origin).any():
        raise AssertionError("training labels cross forecast origin")
    expected = predictions.origin.dt.to_period("M") + predictions.horizon.to_numpy()
    if not (expected == predictions.target_month.dt.to_period("M")).all():
        raise AssertionError("target/horizon alignment failed")
    dynamic = predictions.horizon.le(6)
    expected_provider = predictions.experiment.map({"E19": "seas5", "E20": "ncep"})
    if not predictions.loc[dynamic, "weather_provider"].eq(expected_provider[dynamic]).all():
        raise AssertionError("wrong dynamic provider")
    fallback = ~dynamic
    if fallback.any():
        if not predictions.loc[fallback, "weather_provider"].eq("era5_expanding_normal").all():
            raise AssertionError("unsupported leads do not use climate normals")
        if not predictions.loc[fallback, "fallback_reason"].eq("unsupported_seasonal_lead_7_12").all():
            raise AssertionError("fallback reason missing")
    if predictions.weather_available_at.isna().any():
        raise AssertionError("missing scheduled availability")
