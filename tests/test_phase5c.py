from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.weather.seasonal_skill import (
    add_expanding_normals, chronological_bias_correct, validate_forecast_keys,
)


def _forecast(track: str = "archived") -> pd.DataFrame:
    issues = pd.to_datetime(["2021-01-01", "2021-02-01"], utc=True)
    return pd.DataFrame({
        "provider": ["seas5", "seas5"], "track": [track, track],
        "issue_date": issues, "available_at": issues + pd.Timedelta(days=6) if track == "archived" else pd.NaT,
        "target_month": pd.to_datetime(["2021-01-01", "2021-02-01"]),
        "lead_month": [1, 1], "weather_zone": ["SYSTEM", "SYSTEM"],
    })


def test_key_validation_preserves_hindcast_availability_boundary():
    validate_forecast_keys(_forecast("archived"))
    validate_forecast_keys(_forecast("hindcast_research"))
    invalid = _forecast("hindcast_research")
    invalid["available_at"] = invalid.issue_date
    try:
        validate_forecast_keys(invalid)
    except ValueError as error:
        assert "synthetic" in str(error)
    else:
        raise AssertionError("synthetic hindcast availability was accepted")


def test_expanding_normal_excludes_forecast_month_and_future():
    forecast = _forecast().iloc[[0]]
    history = pd.DataFrame({
        "target_month": pd.to_datetime(["2019-01-01", "2020-01-01", "2021-01-01"]),
        "weather_zone": ["SYSTEM"] * 3, "temperature_mean_c": [10.0, 20.0, 999.0],
    })
    result = add_expanding_normals(forecast, history, ["temperature_mean_c"], 2)
    assert result.temperature_mean_c_normal.iloc[0] == 15.0


def test_bias_correction_cannot_use_unavailable_verification():
    frame = _forecast()
    frame.loc[1, "available_at"] = pd.Timestamp("2021-02-06", tz="UTC")
    frame["temperature_mean_c_actual"] = [10.0, 20.0]
    frame["temperature_mean_c_raw"] = [12.0, 22.0]
    frame["temperature_mean_c_p10"] = [11.0, 21.0]
    frame["temperature_mean_c_p50"] = [12.0, 22.0]
    frame["temperature_mean_c_p90"] = [13.0, 23.0]
    result = chronological_bias_correct(frame, "temperature_mean_c", 1, 7)
    assert result.temperature_mean_c_calibration_samples.tolist() == [0, 0]
    assert result.temperature_mean_c_calibrated.tolist() == [12.0, 22.0]


def test_bias_correction_uses_only_prior_available_error():
    frame = _forecast()
    frame.loc[1, "issue_date"] = pd.Timestamp("2021-03-01", tz="UTC")
    frame.loc[1, "available_at"] = pd.Timestamp("2021-03-07", tz="UTC")
    frame.loc[1, "target_month"] = pd.Timestamp("2021-03-01")
    frame["temperature_mean_c_actual"] = [10.0, 20.0]
    frame["temperature_mean_c_raw"] = [12.0, 22.0]
    result = chronological_bias_correct(frame, "temperature_mean_c", 1, 7)
    assert result.temperature_mean_c_calibration_samples.tolist() == [0, 1]
    assert np.isclose(result.temperature_mean_c_calibrated.iloc[1], 20.0)
