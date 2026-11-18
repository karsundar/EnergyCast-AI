from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from forecasting.backtest import add_intervals, origin_features
from forecasting.baselines import seasonal_naive
from forecasting.features import FeatureConfig, build_monthly_features, build_monthly_targets, calendar_features


class Phase2AFeatureTest(unittest.TestCase):
    def test_system_peak_is_simultaneous_regional_sum(self) -> None:
        timestamps = pd.to_datetime(["2020-01-01T06:00:00Z", "2020-01-01T07:00:00Z"])
        zones = [f"Z{index}" for index in range(8)]
        records = []
        for timestamp_index, timestamp in enumerate(timestamps):
            for zone_index, zone in enumerate(zones):
                demand = 100.0 if zone_index == timestamp_index else 10.0
                records.append({
                    "timestamp_utc": timestamp,
                    "timestamp_local": timestamp.tz_convert("America/Chicago"),
                    "weather_zone": zone,
                    "demand_mw": demand,
                    "energy_mwh": demand,
                })
        targets = build_monthly_targets(pd.DataFrame(records), FeatureConfig(completeness_threshold_pct=0.01))
        system = targets[targets["geography"] == "ERCOT_SYSTEM"].iloc[0]
        self.assertEqual(system["peak_demand_mw"], 170.0)
        self.assertNotEqual(system["peak_demand_mw"], 800.0)

    def test_incomplete_period_is_flagged_even_above_threshold(self) -> None:
        timestamp = pd.Timestamp("2020-01-01T06:00:00Z")
        frame = pd.DataFrame({
            "timestamp_utc": [timestamp] * 8,
            "timestamp_local": [timestamp.tz_convert("America/Chicago")] * 8,
            "weather_zone": [f"Z{index}" for index in range(8)],
            "demand_mw": [1.0] * 8,
            "energy_mwh": [1.0] * 8,
        })
        targets = build_monthly_targets(frame, FeatureConfig(completeness_threshold_pct=0.01))
        self.assertTrue(targets["target_valid"].all())
        self.assertTrue((targets["quality_flag"] == "incomplete_above_threshold").all())

    def test_feature_history_does_not_change_from_future_values(self) -> None:
        months = pd.date_range("2018-01-01", periods=36, freq="MS")
        targets = pd.DataFrame({
            "month": months, "geography": "COAST", "total_energy_mwh": np.arange(36.0) + 100,
            "peak_demand_mw": np.arange(36.0) + 10, "observation_count": 1,
            "expected_observation_count": 1, "completeness_pct": 100.0, "target_valid": True,
            "quality_flag": "complete", "mom_demand_growth": np.nan, "yoy_demand_growth": np.nan,
        })
        before = build_monthly_features(targets)
        changed = targets.copy()
        changed.loc[changed["month"] > pd.Timestamp("2019-12-01"), "total_energy_mwh"] *= 100
        after = build_monthly_features(changed)
        columns = [column for column in before.columns if column.startswith("historical_")]
        left = before[(before["month"] == "2019-12-01") & (before["target_name"] == "total_energy_mwh")][columns]
        right = after[(after["month"] == "2019-12-01") & (after["target_name"] == "total_energy_mwh")][columns]
        pd.testing.assert_frame_equal(left.reset_index(drop=True), right.reset_index(drop=True))

    def test_seasonal_naive_uses_previous_year(self) -> None:
        months = pd.date_range("2019-01-01", periods=24, freq="MS")
        history = pd.Series(np.arange(24.0), index=months)
        targets = pd.date_range("2021-01-01", periods=2, freq="MS")
        np.testing.assert_array_equal(seasonal_naive(history, targets), [12.0, 13.0])

    def test_intervals_are_calibrated_by_geography(self) -> None:
        calibration = pd.DataFrame({
            "model": ["m"] * 4, "target_name": ["t"] * 4, "geography": ["A", "A", "B", "B"],
            "horizon": [1] * 4, "forecast_status": ["complete"] * 4,
            "actual": [11.0, 12.0, 1100.0, 1200.0], "p50": [10.0, 10.0, 1000.0, 1000.0],
        })
        test = pd.DataFrame({
            "model": ["m", "m"], "target_name": ["t", "t"], "geography": ["A", "B"],
            "horizon": [1, 1], "p50": [10.0, 1000.0],
        })
        result = add_intervals(test, calibration)
        self.assertLess(result.loc[0, "p90"] - result.loc[0, "p50"], 10)
        self.assertGreater(result.loc[1, "p90"] - result.loc[1, "p50"], 100)


if __name__ == "__main__":
    unittest.main()
