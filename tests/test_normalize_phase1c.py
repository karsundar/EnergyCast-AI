from __future__ import annotations

import sys
import unittest
from datetime import timezone
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from normalize_phase1c import aggregate_ercot, localize_hour_endings, parse_hour_ending


class NormalizePhase1CTest(unittest.TestCase):
    def test_parse_hour_24_rolls_to_next_day(self) -> None:
        parsed = parse_hour_ending("01/01/2020 24:00")
        self.assertEqual(parsed.isoformat(), "2020-01-02T00:00:00")

    def test_fall_back_hours_are_unique_in_utc(self) -> None:
        starts, flags = localize_hour_endings([
            "11/01/2020 01:00",
            "11/01/2020 02:00",
            "11/01/2020 02:00 DST",
            "11/01/2020 03:00",
        ])
        utc_values = [value.astimezone(timezone.utc) for value in starts]
        self.assertEqual(len(set(utc_values)), 4)
        self.assertIn("dst_fall_back_first", flags)
        self.assertIn("dst_fall_back_second", flags)

    def test_spring_forward_retains_observed_intervals(self) -> None:
        starts, flags = localize_hour_endings([
            "03/08/2020 01:00",
            "03/08/2020 03:00",
            "03/08/2020 04:00",
        ])
        utc_values = [value.astimezone(timezone.utc) for value in starts]
        self.assertEqual([(right - left).total_seconds() for left, right in zip(utc_values, utc_values[1:])], [3600, 3600])
        self.assertIn("dst_spring_forward", flags)

    def test_system_peak_uses_simultaneous_regional_sum(self) -> None:
        utc = pd.to_datetime(["2020-01-01T06:00:00Z", "2020-01-01T07:00:00Z"])
        frame = pd.DataFrame({
            "timestamp_utc": [utc[0], utc[0], utc[1], utc[1]],
            "timestamp_local": [value.tz_convert("America/Chicago") for value in (utc[0], utc[0], utc[1], utc[1])],
            "weather_zone": ["A", "B", "A", "B"],
            "demand_mw": [100.0, 10.0, 10.0, 100.0],
            "energy_mwh": [100.0, 10.0, 10.0, 100.0],
        })
        daily = aggregate_ercot(frame, "D")
        self.assertEqual(daily.loc[0, "peak_demand_mw"], 110.0)
        self.assertEqual(daily.loc[0, "total_energy_mwh"], 220.0)
        self.assertNotEqual(daily.loc[0, "peak_demand_mw"], 200.0)


if __name__ == "__main__":
    unittest.main()
