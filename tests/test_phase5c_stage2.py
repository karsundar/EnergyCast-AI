from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from forecasting.operational_weather import (  # noqa: E402
    WEATHER_FEATURES, assert_e18_leakage_safe, assert_gate_b_leakage_safe, feature_row,
)


def test_demand_features_are_frozen_at_forecast_origin():
    months = pd.date_range("2018-01-01", periods=48, freq="MS")
    series = pd.Series(range(48), index=months, dtype=float)
    peaks = series * 2
    origin = pd.Timestamp("2020-12-01")
    before = feature_row(series, peaks, origin, pd.Timestamp("2021-02-01"),
                         "ERCOT_SYSTEM", 2, months[0])
    series.loc[pd.Timestamp("2021-01-01"):] = 1_000_000
    peaks.loc[pd.Timestamp("2021-01-01"):] = 2_000_000
    after = feature_row(series, peaks, origin, pd.Timestamp("2021-02-01"),
                        "ERCOT_SYSTEM", 2, months[0])
    assert before == after


def test_e18_audit_rejects_future_training_labels():
    frame = pd.DataFrame({
        "oracle_weather": [False], "weather_mode": ["expanding_climate_normal"],
        "training_label_cutoff": [pd.Timestamp("2021-01-01")],
        "origin": [pd.Timestamp("2020-12-01")],
        "target_month": [pd.Timestamp("2021-01-01")], "horizon": [1],
    })
    try:
        assert_e18_leakage_safe(frame)
    except AssertionError as error:
        assert "training labels" in str(error)
    else:
        raise AssertionError("future training-label cutoff was accepted")


def test_gate_a_smoke_has_exact_non_oracle_common_observations():
    report = json.loads((ROOT / "reports/phase5c_stage2/smoke/gate_a_report.json").read_text())
    common = pd.read_parquet(ROOT / "data/processed/phase5c_stage2/smoke/gate_a_common_predictions.parquet")
    assert report["common_experiments"] == ["E0", "E11", "E14", "E18"]
    assert report["oracle_rows"] == 0
    assert set(report["stage2_weather_schema"]) == set(WEATHER_FEATURES)
    keys = ["target_name", "origin", "target_month", "horizon", "geography"]
    assert common.groupby(keys, observed=True).experiment.nunique().eq(4).all()
    assert not common.oracle_weather.fillna(False).any()


def test_gate_b_smoke_changes_only_weather_features():
    root = ROOT / "reports/phase5c_stage2/gate_b_smoke"
    audit = pd.read_parquet(root / "weather_feature_difference_audit.parquet")
    assert set(audit.experiment) == {"E19", "E20"}
    assert audit.unrelated_feature_max_abs_difference.eq(0).all()
    assert audit.reference_prediction_max_abs_difference.eq(0).all()
    assert (audit.dynamic_weather_cells_changed == audit.dynamic_weather_cells).all()
    assert audit.fallback_weather_max_abs_difference.eq(0).all()


def test_gate_b_smoke_is_raw_scheduled_weather_without_oracle_or_calibration():
    output = ROOT / "data/processed/phase5c_stage2/gate_b_smoke"
    frame = pd.concat([
        pd.read_parquet(output / "E19_predictions.parquet"),
        pd.read_parquet(output / "E20_predictions.parquet"),
    ], ignore_index=True)
    assert_gate_b_leakage_safe(frame)
    assert not frame.oracle_weather.any()
    assert frame.calibration_training_cutoff.isna().all()
    assert frame.weather_available_at.notna().all()
    assert (pd.to_datetime(frame.weather_available_at, utc=True)
            <= pd.to_datetime(frame.operational_forecast_time, utc=True)).all()


def test_gate_b_smoke_has_exact_target_specific_baselines():
    common = pd.read_parquet(
        ROOT / "data/processed/phase5c_stage2/gate_b_smoke/gate_b_common_predictions.parquet"
    )
    assert set(common.loc[common.target_name.eq("total_energy_mwh"), "experiment"]) == {
        "E14", "E18", "E19", "E20",
    }
    assert set(common.loc[common.target_name.eq("peak_demand_mw"), "experiment"]) == {
        "E0", "E18", "E19", "E20",
    }
    keys = ["target_name", "origin", "target_month", "horizon", "geography"]
    assert common.groupby(keys, observed=True).experiment.nunique().eq(4).all()
