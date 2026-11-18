from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_phase5c_stage15 import prior_provider  # noqa: E402


def test_comparable_era5_schema_and_aggregation_provenance():
    path = ROOT / "data/processed/weather/seasonal_compatible_era5/year=2021/part.parquet"
    frame = pd.read_parquet(path)
    assert len(frame) == 108
    assert frame.weather_zone.nunique() == 9
    assert frame.target_month.nunique() == 12
    assert frame.aggregation_order.str.contains("UTC monthly grid means").all()
    assert frame[["temperature_mean_c", "relative_humidity_pct", "wind_speed_mps"]].notna().all().all()


def test_prior_provider_uses_only_observable_archived_errors():
    frame = pd.DataFrame({
        "weather_zone": ["SYSTEM"] * 4, "lead_month": [1] * 4,
        "provider": ["ncep", "ncep", "seas5", "seas5"],
        "target_month": pd.to_datetime(["2020-01-01", "2021-01-01", "2020-01-01", "2021-01-01"]),
        "temperature_mean_c_actual": [10.0] * 4,
        "temperature_mean_c_calibrated": [11.0, 1000.0, 12.0, -1000.0],
    })
    row = pd.Series({"weather_zone": "SYSTEM", "lead_month": 1,
                     "available_at": pd.Timestamp("2020-02-10", tz="UTC")})
    provider, cutoff = prior_provider(frame, row, "temperature_mean_c", minimum=1)
    assert provider == "ncep"
    assert cutoff == pd.Timestamp("2020-02-07", tz="UTC")


def test_smoke_operational_features_have_no_leakage_or_daily_fabrication():
    root = ROOT / "reports/phase5c_stage15/smoke"
    audit = __import__("json").loads((root / "readiness_audit.json").read_text())
    frame = pd.read_parquet(root / "operational_weather_features.parquet")
    assert audit["future_calibration_cutoff_violations"] == 0
    assert not audit["daily_features_fabricated"]
    assert set(frame.forecast_lead) == set(range(1, 13))
    fallback = frame[(frame.feature_mode == "validation_selected_temperature") & (frame.forecast_lead > 6)]
    assert set(fallback.fallback_reason) == {"unsupported_seasonal_lead_7_12"}
