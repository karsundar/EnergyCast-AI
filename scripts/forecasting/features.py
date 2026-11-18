"""Leakage-safe monthly target and feature construction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar


LAGS = (1, 2, 3, 6, 12, 24)
WINDOWS = (3, 6, 12, 24)
SEASONS = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
           6: "summer", 7: "summer", 8: "summer", 9: "fall", 10: "fall", 11: "fall"}


@dataclass(frozen=True)
class FeatureConfig:
    completeness_threshold_pct: float = 99.0
    local_timezone: str = "America/Chicago"


def expected_hours_in_month(month: pd.Timestamp, timezone_name: str = "America/Chicago") -> int:
    start = pd.Timestamp(month).tz_localize(timezone_name)
    end = (pd.Timestamp(month) + pd.offsets.MonthBegin(1)).tz_localize(timezone_name)
    return int((end.tz_convert("UTC") - start.tz_convert("UTC")).total_seconds() // 3600)


def _monthly_summary(frame: pd.DataFrame, keys: list[str], expected_zones: int = 1) -> pd.DataFrame:
    return frame.groupby(keys, as_index=False).agg(
        total_energy_mwh=("energy_mwh", lambda values: values.sum(min_count=1)),
        peak_demand_mw=("demand_mw", "max"),
        observation_count=("demand_mw", "count"),
        expected_zone_observations=("demand_mw", lambda values: expected_zones),
    )


def build_monthly_targets(hourly: pd.DataFrame, config: FeatureConfig = FeatureConfig()) -> pd.DataFrame:
    required = {"timestamp_utc", "timestamp_local", "weather_zone", "demand_mw", "energy_mwh"}
    missing = required - set(hourly.columns)
    if missing:
        raise ValueError(f"ERCOT hourly data is missing columns: {sorted(missing)}")
    data = hourly.copy()
    data["month"] = data["timestamp_local"].dt.tz_localize(None).dt.to_period("M").dt.to_timestamp()
    zone_count = int(data["weather_zone"].nunique())
    if zone_count != 8:
        raise ValueError(f"expected eight ERCOT weather zones, found {zone_count}")

    zonal = data.groupby(["month", "weather_zone"], as_index=False).agg(
        total_energy_mwh=("energy_mwh", lambda values: values.sum(min_count=1)),
        peak_demand_mw=("demand_mw", "max"),
        observation_count=("demand_mw", "count"),
    ).rename(columns={"weather_zone": "geography"})

    system_hourly = data.groupby(["timestamp_utc", "timestamp_local", "month"], as_index=False).agg(
        demand_mw=("demand_mw", lambda values: values.sum(min_count=zone_count)),
        energy_mwh=("energy_mwh", lambda values: values.sum(min_count=zone_count)),
    )
    system = system_hourly.groupby("month", as_index=False).agg(
        total_energy_mwh=("energy_mwh", lambda values: values.sum(min_count=1)),
        peak_demand_mw=("demand_mw", "max"),
        observation_count=("demand_mw", "count"),
    )
    system["geography"] = "ERCOT_SYSTEM"
    targets = pd.concat([zonal, system], ignore_index=True)
    targets["expected_observation_count"] = targets["month"].map(
        lambda value: expected_hours_in_month(pd.Timestamp(value), config.local_timezone)
    )
    targets["completeness_pct"] = (
        targets["observation_count"] / targets["expected_observation_count"] * 100.0
    ).clip(upper=100.0)
    targets["target_valid"] = targets["completeness_pct"] >= config.completeness_threshold_pct
    targets["quality_flag"] = np.select(
        [targets["completeness_pct"] >= 100.0, targets["target_valid"]],
        ["complete", "incomplete_above_threshold"],
        default="excluded_incomplete_period",
    )
    targets = targets.sort_values(["geography", "month"]).reset_index(drop=True)

    valid_energy = targets["total_energy_mwh"].where(targets["target_valid"])
    grouped_energy = valid_energy.groupby(targets["geography"])
    targets["mom_demand_growth"] = grouped_energy.pct_change(fill_method=None)
    targets["yoy_demand_growth"] = grouped_energy.pct_change(12, fill_method=None)
    return targets


def calendar_features(months: pd.Series) -> pd.DataFrame:
    unique_months = pd.Series(pd.to_datetime(months.unique())).sort_values()
    calendar = USFederalHolidayCalendar()
    records: list[dict[str, object]] = []
    for month in unique_months:
        start = pd.Timestamp(month)
        end = start + pd.offsets.MonthEnd(1)
        dates = pd.date_range(start, end, freq="D")
        holidays = calendar.holidays(start=start, end=end)
        records.append({
            "month": start,
            "known_future_month_of_year": start.month,
            "known_future_quarter": start.quarter,
            "known_future_season": SEASONS[start.month],
            "known_future_days_in_month": start.days_in_month,
            "known_future_weekday_count": int((dates.day_of_week < 5).sum()),
            "known_future_weekend_count": int((dates.day_of_week >= 5).sum()),
            "known_future_holiday_count": len(holidays),
            "known_future_month_sin": float(np.sin(2 * np.pi * start.month / 12)),
            "known_future_month_cos": float(np.cos(2 * np.pi * start.month / 12)),
        })
    return pd.DataFrame.from_records(records)


def build_monthly_features(targets: pd.DataFrame) -> pd.DataFrame:
    calendar = calendar_features(targets["month"])
    base = targets.merge(calendar, on="month", how="left")
    first_month = base["month"].min()
    base["known_future_trend_month"] = (
        (base["month"].dt.year - first_month.year) * 12 + base["month"].dt.month - first_month.month
    )
    rows: list[pd.DataFrame] = []
    for target_name, value_column in (("total_energy_mwh", "total_energy_mwh"), ("peak_demand_mw", "peak_demand_mw")):
        frame = base.copy()
        frame["target_name"] = target_name
        frame["target_value"] = frame[value_column]
        valid = frame[value_column].where(frame["target_valid"])
        grouped = valid.groupby(frame["geography"])
        for lag in LAGS:
            frame[f"historical_demand_lag_{lag}"] = grouped.shift(lag)
        for window in WINDOWS:
            frame[f"historical_demand_trailing_mean_{window}"] = grouped.transform(
                lambda values, size=window: values.shift(1).rolling(size, min_periods=size).mean()
            )
        frame["historical_mom_growth"] = grouped.pct_change(fill_method=None).groupby(frame["geography"]).shift(1)
        frame["historical_yoy_growth"] = grouped.pct_change(12, fill_method=None).groupby(frame["geography"]).shift(1)
        peak_valid = frame["peak_demand_mw"].where(frame["target_valid"])
        frame["historical_peak_demand"] = peak_valid.groupby(frame["geography"]).shift(1)
        rows.append(frame)
    result = pd.concat(rows, ignore_index=True)
    return result.sort_values(["target_name", "geography", "month"]).reset_index(drop=True)


def prepare_external_features(source: pd.DataFrame, name_column: str) -> pd.DataFrame:
    frame = source.rename(columns={name_column: "feature_name", "observation_date": "month"}).copy()
    frame["month"] = pd.to_datetime(frame["month"]).dt.to_period("M").dt.to_timestamp()
    frame["known_at_publication"] = frame["publication_date"].notna()
    frame["baseline_included"] = False
    keep = ["month", "feature_name", "value", "publication_date", "sampling_frequency", "units",
            "vintage_available", "known_at_publication", "baseline_included", "source_url", "source_file"]
    return frame[keep].sort_values(["feature_name", "month"]).reset_index(drop=True)


def write_feature_outputs(processed_dir: Path, config: FeatureConfig = FeatureConfig()) -> tuple[pd.DataFrame, pd.DataFrame]:
    hourly = pd.read_parquet(processed_dir / "ercot_hourly.parquet")
    targets = build_monthly_targets(hourly, config)
    features = build_monthly_features(targets)
    targets.to_parquet(processed_dir / "ercot_monthly_targets.parquet", index=False, compression="zstd")
    features.to_parquet(processed_dir / "ercot_monthly_features.parquet", index=False, compression="zstd")

    economic = pd.read_parquet(processed_dir / "economic_indicators.parquet")
    prepare_external_features(economic, "dataset_id").to_parquet(
        processed_dir / "economic_monthly_features.parquet", index=False, compression="zstd"
    )
    geopolitical = pd.read_parquet(processed_dir / "geopolitical_indicators.parquet")
    prepare_external_features(geopolitical, "indicator").to_parquet(
        processed_dir / "geopolitical_monthly_features.parquet", index=False, compression="zstd"
    )
    return targets, features
