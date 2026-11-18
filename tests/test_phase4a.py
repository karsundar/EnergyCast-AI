from __future__ import annotations

import pandas as pd

from scripts.forecasting.weather_oracle import (
    aggregate_daily_predictions, assert_oracle_leakage_safe, canonical_zone,
    daily_forecast_dates, monthly_direct_samples, prepare_daily, prepare_monthly,
)


def synthetic_monthly() -> tuple[pd.DataFrame, pd.DataFrame]:
    months = pd.date_range("2018-01-01", periods=40, freq="MS")
    targets = pd.DataFrame({
        "month": months, "geography": "ERCOT_SYSTEM", "total_energy_mwh": range(100, 140),
        "peak_demand_mw": range(50, 90), "target_valid": True,
    })
    weather = pd.DataFrame({
        "month": months.tz_localize("America/Chicago").tz_convert("UTC"), "weather_zone": "SYSTEM",
        "temperature_mean_c": 20.0, "temperature_min_c": 10.0, "temperature_max_c": 30.0,
        "relative_humidity_pct": 50.0, "wind_speed_mps": 3.0, "precipitation_mm": 2.0,
        "solar_energy_mj_m2": 15.0, "hdd_65f": 1.0, "cdd_65f": 2.0,
        "extreme_heat_days": 0.0, "freezing_days": 0.0, "heatwave_days": 0.0,
        "coldwave_days": 0.0, "mapping_approximate": True,
    })
    return weather, targets


def test_monthly_oracle_features_are_weather_only_and_target_is_canonical() -> None:
    weather, targets = synthetic_monthly()
    frame = prepare_monthly(weather, targets)
    samples = monthly_direct_samples(frame, "total_energy_mwh", horizon=2, include_weather=True)
    assert canonical_zone("SYSTEM") == "ERCOT_SYSTEM"
    assert samples["oracle_temperature_mean_c"].eq(20.0).all()
    assert_oracle_leakage_safe(samples)


def test_daily_aggregation_sums_energy_and_maximizes_peak() -> None:
    base = {"experiment":"E5", "model":"lightgbm_daily_oracle", "origin":pd.Timestamp("2020-12-31"),
            "target_month":pd.Timestamp("2021-01-01"), "horizon":1, "geography":"ERCOT_SYSTEM",
            "oracle_weather":True, "forecast_status":"complete"}
    rows = [
        {**base, "target_name":"total_energy_mwh", "p50":10.0, "actual":11.0},
        {**base, "target_name":"total_energy_mwh", "p50":12.0, "actual":13.0},
        {**base, "target_name":"peak_demand_mw", "p50":5.0, "actual":6.0},
        {**base, "target_name":"peak_demand_mw", "p50":8.0, "actual":7.0},
    ]
    result = aggregate_daily_predictions(pd.DataFrame(rows))
    energy = result[result.target_name == "total_energy_mwh"].iloc[0]
    peak = result[result.target_name == "peak_demand_mw"].iloc[0]
    assert (energy.p50, energy.actual) == (22.0, 24.0)
    assert (peak.p50, peak.actual) == (8.0, 7.0)


def test_daily_demand_completeness_respects_dst_and_missing_interval() -> None:
    frame = pd.DataFrame({
        "date": ["2021-03-14", "2021-11-07", "2021-01-02"], "weather_zone": "SYSTEM",
        "observation_count": [23, 25, 23], "completeness_pct": 100.0,
        "total_energy_mwh": 1.0, "peak_demand_mw": 1.0,
    })
    result = prepare_daily(frame)
    assert result["expected_observation_count"].tolist() == [24, 23, 25] or sorted(result["expected_observation_count"].tolist()) == [23, 24, 25]
    lookup = result.set_index("date")
    assert lookup.loc[pd.Timestamp("2021-03-14"), "target_valid"]
    assert lookup.loc[pd.Timestamp("2021-11-07"), "target_valid"]
    assert not lookup.loc[pd.Timestamp("2021-01-02"), "target_valid"]


def test_daily_forecast_dates_preserve_complete_future_months_and_leap_day() -> None:
    dates = daily_forecast_dates(pd.Timestamp("2023-02-28"), 12)
    assert dates.min() == pd.Timestamp("2023-03-01")
    assert dates.max() == pd.Timestamp("2024-02-29")
    assert len(dates) == 366
