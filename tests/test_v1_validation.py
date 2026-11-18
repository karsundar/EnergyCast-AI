from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.forecasting.v1_calibration import (
    apply_scale_aware_intervals, fit_scale_aware_intervals,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/processed/v1/smoke"
REPORTS = ROOT / "reports/v1/smoke"


def synthetic_calibration() -> pd.DataFrame:
    rows = []
    for geography, scale in (("COAST", 100.0), ("ERCOT_SYSTEM", 1000.0)):
        for horizon in (1, 2):
            for month in pd.date_range("2020-01-01", "2020-12-01", freq="MS"):
                p50 = scale * (1 + month.month / 100)
                rows.append({
                    "target_name": "total_energy_mwh", "origin": month - pd.DateOffset(months=horizon),
                    "target_month": month, "horizon": horizon, "geography": geography,
                    "p50": p50, "actual": p50 * (1.0 + .01 * horizon),
                })
    return pd.DataFrame(rows)


def test_v1_calibration_is_geography_and_horizon_specific():
    frame = synthetic_calibration()
    fitted = fit_scale_aware_intervals(frame, target_coverage=.8, minimum_horizon_samples=8)
    assert len(fitted.scales) == 2
    assert len(fitted.by_horizon) == 4
    result = apply_scale_aware_intervals(frame, fitted)
    assert set(result.calibration_scope) == {"geography_horizon"}
    assert (result.p10 <= result.p50).all()
    assert (result.p50 <= result.p90).all()


def test_v1_calibration_rejects_non_reserved_period():
    frame = synthetic_calibration()
    frame.loc[0, "target_month"] = pd.Timestamp("2021-01-01")
    try:
        fit_scale_aware_intervals(frame)
    except ValueError as error:
        assert "reserved 2020" in str(error)
    else:
        raise AssertionError("future-test row entered calibration")


def test_v1_smoke_selection_and_leakage_audit():
    report = json.loads((REPORTS / "v1_validation_report.json").read_text())
    assert set(report["selected_models"]) == {"total_energy_mwh", "peak_demand_mw"}
    assert report["calibration_period"] == ["2020-01-01", "2020-12-01"]
    assert report["final_test_start"] == "2021-01-01"
    assert report["calibration_uses_final_test"] is False
    assert report["oracle_rows"] == 0
    assert report["quantile_crossing_rows"] == 0
    assert report["weather_available_after_issuance_rows"] == 0
    assert report["twelve_month_generation_valid"] is True
    assert report["experiments_not_started"] == ["E26", "E27"]


def test_v1_system_and_regional_calibration_are_not_pooled():
    horizon = pd.read_csv(REPORTS / "horizon_calibration.csv")
    fallback = pd.read_csv(REPORTS / "geography_fallback_calibration.csv")
    assert "ERCOT_SYSTEM" in set(horizon.geography)
    assert "ERCOT_SYSTEM" in set(fallback.geography)
    assert fallback.groupby("target_name", observed=True).geography.nunique().ge(9).all()


def test_v1_issuance_and_provider_audits_are_explicit():
    issuance = pd.read_csv(REPORTS / "issuance_audit.csv")
    availability = pd.read_csv(REPORTS / "availability_audit.csv")
    assert set(issuance.schedule) == {"month_start", "day_15_refresh"}
    assert issuance.eligible.all()
    assert not availability.future_observed_weather.any()
    assert set(availability.model) == {"E14", "E24-B"}
