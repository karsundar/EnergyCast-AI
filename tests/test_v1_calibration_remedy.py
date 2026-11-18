from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.forecasting.v1_calibration_remedy import (
    add_horizon_band, apply_remedy, fit_remedy, label_available_at,
)

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports/v1/calibration_remedy/smoke"
BANDS = {"1-3": [1, 3], "4-6": [4, 6], "7-9": [7, 9], "10-12": [10, 12]}


def synthetic_rows() -> pd.DataFrame:
    rows = []
    for geography, scale in (("COAST", 100.0), ("ERCOT_SYSTEM", 1000.0)):
        for origin in pd.date_range("2017-01-01", "2018-12-01", freq="MS"):
            for horizon in range(1, 13):
                p50 = scale * (1 + origin.month / 100)
                rows.append({
                    "target_name": "total_energy_mwh", "geography": geography,
                    "origin": origin, "target_month": origin + pd.DateOffset(months=horizon),
                    "horizon": horizon, "p50": p50,
                    "actual": p50 * (1 + .01 * horizon),
                })
    frame = add_horizon_band(pd.DataFrame(rows), BANDS)
    frame["label_available_at"] = label_available_at(frame.target_month)
    return frame


def test_remedy_methods_preserve_order_and_geography_groups():
    frame = synthetic_rows()
    for method in (
        "scale_normalized_symmetric", "scale_normalized_asymmetric", "relative_asymmetric",
    ):
        fitted = fit_remedy(frame, method, .8, 12)
        result = apply_remedy(frame.tail(48), fitted)
        assert (result.p10 <= result.p50).all()
        assert (result.p50 <= result.p90).all()
        assert set(fitted.fallback.geography) == {"COAST", "ERCOT_SYSTEM"}


def test_horizon_bands_are_predefined_and_complete():
    frame = add_horizon_band(pd.DataFrame({"horizon": range(1, 13)}), BANDS)
    assert frame.groupby("horizon_band", observed=True).size().eq(3).all()


def test_label_availability_is_after_target_month():
    frame = synthetic_rows()
    assert (frame.label_available_at > frame.target_month).all()


def test_smoke_uses_no_final_test_and_keeps_intervals_disabled():
    report = json.loads((REPORTS / "calibration_remedy_report.json").read_text())
    assert report["input_target_end"] == "2020-12-01"
    assert report["final_test_rows_read"] == 0
    assert report["future_calibration_cutoff_violations"] == 0
    assert report["independent_holdout_available"] is False
    assert report["probabilistic_intervals_enabled"] is False
    assert report["deterministic_forecasts_enabled"] is True
    assert report["final_test_results_preserved_unchanged"] is True
    assert report["experiments_not_started"] == ["E26", "E27"]


def test_smoke_selects_system_and_regional_methods_separately():
    selected = pd.read_csv(REPORTS / "selected_methods.csv")
    assert set(selected.scope) == {"system", "regional"}
    assert selected.groupby("target_name", observed=True).scope.nunique().eq(2).all()
    assert selected.folds.eq(1).all()
