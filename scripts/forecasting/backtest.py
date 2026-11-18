"""Rolling-origin multi-horizon backtesting and evaluation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .baselines import (LGBM_FEATURES, MODEL_NAMES, ZONE_CODES, drift_seasonal_naive, fit_lightgbm,
                        holt_winters, seasonal_naive)
from .features import LAGS, SEASONS, WINDOWS, calendar_features


TARGET_COLUMNS = {"total_energy_mwh": "total_energy_mwh", "peak_demand_mw": "peak_demand_mw"}
DEFAULT_LGBM_CANDIDATES = (
    {"n_estimators": 150, "learning_rate": 0.05, "num_leaves": 15, "max_depth": 5},
    {"n_estimators": 250, "learning_rate": 0.03, "num_leaves": 15, "max_depth": 6},
    {"n_estimators": 200, "learning_rate": 0.05, "num_leaves": 7, "max_depth": 4},
)


@dataclass(frozen=True)
class BacktestConfig:
    max_horizon: int = 12
    tuning_start: str = "2018-01-01"
    tuning_end: str = "2019-12-01"
    calibration_start: str = "2020-01-01"
    calibration_end: str = "2020-12-01"
    test_start: str = "2021-01-01"


def _series_map(targets: pd.DataFrame, target_name: str) -> dict[str, pd.Series]:
    value_column = TARGET_COLUMNS[target_name]
    result: dict[str, pd.Series] = {}
    for geography, frame in targets.groupby("geography"):
        values = frame.set_index("month")[value_column].astype(float)
        values.loc[~frame.set_index("month")["target_valid"]] = np.nan
        result[str(geography)] = values.sort_index()
    return result


def _calendar_lookup(feature_store: pd.DataFrame) -> dict[pd.Timestamp, dict[str, Any]]:
    columns = [column for column in feature_store.columns if column.startswith("known_future_")]
    unique = feature_store[["month", *columns]].drop_duplicates("month")
    return {pd.Timestamp(row["month"]): row.to_dict() for _, row in unique.iterrows()}


def origin_features(
    series: pd.Series,
    peak_series: pd.Series,
    geography: str,
    origin: pd.Timestamp,
    target_month: pd.Timestamp,
    horizon: int,
    calendar: dict[pd.Timestamp, dict[str, Any]],
    first_month: pd.Timestamp,
) -> dict[str, float]:
    history = series.loc[:origin]
    row: dict[str, float] = {
        "horizon": float(horizon),
        "geography_code": float(ZONE_CODES[geography]),
        "future_trend": float((target_month.year - first_month.year) * 12 + target_month.month - first_month.month),
        "origin_value": float(history.get(origin, np.nan)),
        "origin_mom_growth": float(history.get(origin, np.nan) / history.get(origin - pd.DateOffset(months=1), np.nan) - 1),
        "origin_yoy_growth": float(history.get(origin, np.nan) / history.get(origin - pd.DateOffset(months=12), np.nan) - 1),
        "origin_peak_demand": float(peak_series.get(origin, np.nan)),
    }
    for lag in LAGS:
        row[f"origin_lag_{lag}"] = float(history.get(origin - pd.DateOffset(months=lag), np.nan))
    for window in WINDOWS:
        window_values = history.loc[:origin].tail(window)
        row[f"origin_trailing_mean_{window}"] = float(window_values.mean()) if len(window_values) == window and window_values.notna().all() else np.nan
    if target_month not in calendar:
        calendar[target_month] = calendar_features(pd.Series([target_month])).iloc[0].to_dict()
    known = calendar[target_month]
    for source, destination in (
        ("known_future_month_of_year", "future_month"), ("known_future_quarter", "future_quarter"),
        ("known_future_days_in_month", "future_days_in_month"),
        ("known_future_weekday_count", "future_weekday_count"),
        ("known_future_weekend_count", "future_weekend_count"),
        ("known_future_holiday_count", "future_holiday_count"),
        ("known_future_month_sin", "future_month_sin"), ("known_future_month_cos", "future_month_cos"),
    ):
        row[destination] = float(known[source])
    return row


def build_direct_samples(targets: pd.DataFrame, feature_store: pd.DataFrame, target_name: str, max_horizon: int = 12) -> pd.DataFrame:
    series_by_geo = _series_map(targets, target_name)
    peaks_by_geo = _series_map(targets, "peak_demand_mw")
    calendar = _calendar_lookup(feature_store)
    first_month = targets["month"].min()
    records: list[dict[str, Any]] = []
    for geography, series in series_by_geo.items():
        months = series.index
        for origin in months[24:-1]:
            for horizon in range(1, max_horizon + 1):
                target_month = origin + pd.DateOffset(months=horizon)
                if target_month not in series.index or not np.isfinite(series.get(target_month, np.nan)):
                    continue
                row = origin_features(series, peaks_by_geo[geography], geography, origin, target_month, horizon, calendar, first_month)
                if not np.isfinite(row["origin_value"]):
                    continue
                row.update({"origin": origin, "target_month": target_month, "label": float(series[target_month]), "geography": geography})
                records.append(row)
    return pd.DataFrame.from_records(records)


def tune_lightgbm(samples: pd.DataFrame, config: BacktestConfig) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    train = samples[samples["target_month"] < pd.Timestamp(config.tuning_start)]
    validation = samples[
        samples["origin"].between(pd.Timestamp(config.tuning_start), pd.Timestamp(config.tuning_end))
        & (samples["target_month"] <= pd.Timestamp(config.tuning_end))
    ]
    scores: list[dict[str, Any]] = []
    for candidate in DEFAULT_LGBM_CANDIDATES:
        model = fit_lightgbm(train, dict(candidate))
        predicted = model.predict(validation[LGBM_FEATURES])
        wape = float(np.abs(validation["label"].to_numpy() - predicted).sum() / np.abs(validation["label"]).sum())
        scores.append({"parameters": dict(candidate), "development_wape": wape})
    best = min(scores, key=lambda item: item["development_wape"])
    return dict(best["parameters"]), scores


def _forecast_origins(start_target: str, final_month: pd.Timestamp) -> pd.DatetimeIndex:
    first_origin = pd.Timestamp(start_target) - pd.DateOffset(months=1)
    return pd.date_range(first_origin, final_month, freq="MS")


def rolling_predictions(
    targets: pd.DataFrame,
    feature_store: pd.DataFrame,
    target_name: str,
    origins: pd.DatetimeIndex,
    lgbm_params: dict[str, Any],
    config: BacktestConfig,
) -> pd.DataFrame:
    series_by_geo = _series_map(targets, target_name)
    peaks_by_geo = _series_map(targets, "peak_demand_mw")
    samples = build_direct_samples(targets, feature_store, target_name, config.max_horizon)
    calendar = _calendar_lookup(feature_store)
    first_month = targets["month"].min()
    final_actual = targets["month"].max()
    records: list[dict[str, Any]] = []
    for origin in origins:
        training = samples[samples["target_month"] <= origin]
        lightgbm_model = fit_lightgbm(training, lgbm_params)
        prediction_rows: list[dict[str, float]] = []
        prediction_keys: list[tuple[str, int, pd.Timestamp]] = []
        for geography, series in series_by_geo.items():
            history = series.loc[:origin]
            target_months = pd.date_range(origin + pd.DateOffset(months=1), periods=config.max_horizon, freq="MS")
            statistical = {
                "seasonal_naive": seasonal_naive(history, target_months),
                "drift_seasonal_naive": drift_seasonal_naive(history, target_months),
                "holt_winters": holt_winters(history, config.max_horizon),
            }
            for horizon, target_month in enumerate(target_months, 1):
                prediction_rows.append(origin_features(
                    series, peaks_by_geo[geography], geography, origin, target_month, horizon,
                    calendar, first_month,
                ))
                prediction_keys.append((geography, horizon, target_month))
            for model_name, values in statistical.items():
                for horizon, (target_month, predicted) in enumerate(zip(target_months, values), 1):
                    actual = series.get(target_month, np.nan)
                    status = "complete"
                    if target_month > final_actual or not np.isfinite(actual):
                        status = "incomplete_horizon"
                    elif not np.isfinite(predicted):
                        status = "model_unavailable"
                    records.append({
                        "origin": origin, "target_month": target_month, "horizon": horizon,
                        "geography": geography, "target_name": target_name, "model": model_name,
                        "p50": float(predicted), "actual": float(actual) if np.isfinite(actual) else np.nan,
                        "forecast_status": status,
                    })
        lightgbm_values = lightgbm_model.predict(pd.DataFrame(prediction_rows)[LGBM_FEATURES])
        for (geography, horizon, target_month), predicted in zip(prediction_keys, lightgbm_values):
            actual = series_by_geo[geography].get(target_month, np.nan)
            records.append({
                "origin": origin, "target_month": target_month, "horizon": horizon,
                "geography": geography, "target_name": target_name, "model": "lightgbm_direct",
                "p50": float(predicted), "actual": float(actual) if np.isfinite(actual) else np.nan,
                "forecast_status": "complete" if target_month <= final_actual and np.isfinite(actual) else "incomplete_horizon",
            })
    return pd.DataFrame.from_records(records)


def add_intervals(test: pd.DataFrame, calibration: pd.DataFrame) -> pd.DataFrame:
    residuals = calibration[calibration["forecast_status"] == "complete"].copy()
    residuals["residual"] = residuals["actual"] - residuals["p50"]
    grouped = residuals.groupby(["model", "target_name", "geography", "horizon"])["residual"]
    quantiles = grouped.quantile([0.1, 0.9]).unstack().rename(columns={0.1: "q10", 0.9: "q90"}).reset_index()
    result = test.merge(quantiles, on=["model", "target_name", "geography", "horizon"], how="left")
    result["p10"] = result["p50"] + result["q10"]
    result["p90"] = result["p50"] + result["q90"]
    return result.drop(columns=["q10", "q90"])


def _metric_values(frame: pd.DataFrame, mase_scale: float) -> dict[str, float]:
    actual = frame["actual"].to_numpy(dtype=float)
    forecast = frame["p50"].to_numpy(dtype=float)
    error = forecast - actual
    absolute = np.abs(error)
    nonzero = actual != 0
    coverage = ((actual >= frame["p10"].to_numpy()) & (actual <= frame["p90"].to_numpy())).mean()
    def quantile_loss(q: float, values: np.ndarray) -> float:
        residual = actual - values
        return float(np.mean(np.maximum(q * residual, (q - 1) * residual)))
    return {
        "observations": float(len(frame)), "mae": float(absolute.mean()),
        "rmse": float(np.sqrt(np.mean(error ** 2))),
        "mape": float(np.mean(absolute[nonzero] / np.abs(actual[nonzero])) * 100) if nonzero.any() else np.nan,
        "wape": float(absolute.sum() / np.abs(actual).sum() * 100),
        "mase": float(absolute.mean() / mase_scale) if mase_scale > 0 else np.nan,
        "forecast_bias": float(error.mean()), "directional_accuracy": float(frame["direction_correct"].mean()),
        "yoy_growth_mae": float(frame["yoy_growth_error"].abs().mean()),
        "peak_demand_mae": float(absolute.mean()) if frame["target_name"].iloc[0] == "peak_demand_mw" else np.nan,
        "interval_coverage": float(coverage),
        "p10_quantile_loss": quantile_loss(0.1, frame["p10"].to_numpy()),
        "p50_quantile_loss": quantile_loss(0.5, forecast),
        "p90_quantile_loss": quantile_loss(0.9, frame["p90"].to_numpy()),
    }


def evaluate_predictions(predictions: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    complete = predictions[predictions["forecast_status"] == "complete"].copy()
    lookup: dict[tuple[str, str, pd.Timestamp], float] = {}
    for _, row in targets.iterrows():
        for target_name, column in TARGET_COLUMNS.items():
            lookup[(str(row["geography"]), target_name, pd.Timestamp(row["month"]))] = float(row[column])
    complete["season"] = complete["target_month"].dt.month.map(SEASONS)
    complete["covid_period"] = complete["target_month"].between(pd.Timestamp("2020-03-01"), pd.Timestamp("2021-06-01"))
    thresholds = targets[targets["month"] < pd.Timestamp("2021-01-01")].groupby(
        ["geography", targets["month"].dt.month]
    )["peak_demand_mw"].quantile(0.95)
    complete["extreme_load_proxy"] = [
        lookup[(row.geography, "peak_demand_mw", row.target_month)] > thresholds.get((row.geography, row.target_month.month), np.inf)
        for row in complete.itertuples()
    ]
    complete["actual_previous"] = [lookup.get((row.geography, row.target_name, row.target_month - pd.DateOffset(months=1)), np.nan) for row in complete.itertuples()]
    complete["actual_prior_year"] = [lookup.get((row.geography, row.target_name, row.target_month - pd.DateOffset(months=12)), np.nan) for row in complete.itertuples()]
    previous_predictions = complete.set_index(["origin", "geography", "target_name", "model", "horizon"])["p50"]
    predicted_previous = []
    for row in complete.itertuples():
        if row.horizon == 1:
            predicted_previous.append(lookup.get((row.geography, row.target_name, row.origin), np.nan))
        else:
            predicted_previous.append(previous_predictions.get((row.origin, row.geography, row.target_name, row.model, row.horizon - 1), np.nan))
    complete["predicted_previous"] = predicted_previous
    complete["direction_correct"] = np.sign(complete["p50"] - complete["predicted_previous"]) == np.sign(complete["actual"] - complete["actual_previous"])
    complete["yoy_growth_error"] = (
        complete["p50"] / complete["actual_prior_year"] - 1
    ) - (complete["actual"] / complete["actual_prior_year"] - 1)

    training = targets[targets["month"] < pd.Timestamp("2021-01-01")]
    mase_scales: dict[tuple[str, str], float] = {}
    for geography, frame in training.groupby("geography"):
        for target_name, column in TARGET_COLUMNS.items():
            series = frame.set_index("month")[column].where(frame.set_index("month")["target_valid"])
            mase_scales[(str(geography), target_name)] = float((series - series.shift(12)).abs().mean())

    records: list[dict[str, Any]] = []
    breakdowns = {
        "zone_horizon": ["model", "target_name", "geography", "horizon"],
        "zone_horizon_season": ["model", "target_name", "geography", "horizon", "season"],
        "system_horizon": ["model", "target_name", "horizon"],
        "period_category": ["model", "target_name", "period_category"],
    }
    period_frames = [
        complete.assign(period_category="all_test"),
        complete[complete["covid_period"]].assign(period_category="covid_2021_overlap"),
        complete[complete["extreme_load_proxy"]].assign(period_category="extreme_load_proxy"),
    ]
    metric_sources = {
        "zone_horizon": complete,
        "zone_horizon_season": complete,
        "system_horizon": complete[complete["geography"] == "ERCOT_SYSTEM"],
        "period_category": pd.concat(period_frames, ignore_index=True).query("geography == 'ERCOT_SYSTEM'"),
    }
    for breakdown, group_columns in breakdowns.items():
        for keys, frame in metric_sources[breakdown].groupby(group_columns, dropna=False):
            keys = keys if isinstance(keys, tuple) else (keys,)
            descriptor = dict(zip(group_columns, keys))
            geography = str(descriptor.get("geography", "ERCOT_SYSTEM"))
            target_name = str(descriptor["target_name"])
            records.append({"breakdown": breakdown, **descriptor, **_metric_values(frame, mase_scales[(geography, target_name)])})
    return pd.DataFrame.from_records(records)
