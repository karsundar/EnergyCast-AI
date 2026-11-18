from __future__ import annotations

import unittest
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]


class Phase3BValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = duckdb.connect()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_spatial_weights_and_provenance(self) -> None:
        path = ROOT / "data/processed/weather/geospatial/ercot_weather_zone_grid_weights.parquet"
        rows = self.connection.execute(
            f"SELECT weather_zone, SUM(zone_weight), BOOL_AND(grid_cell_fraction>0), COUNT(*) "
            f"FROM read_parquet('{path}') GROUP BY weather_zone ORDER BY weather_zone"
        ).fetchall()
        self.assertEqual(len(rows), 9)
        self.assertEqual({row[0] for row in rows}, {
            "COAST", "EAST", "FAR_WEST", "NORTH", "NORTH_CENTRAL",
            "SOUTHERN", "SOUTH_CENTRAL", "WEST", "SYSTEM",
        })
        for _, weight, positive, count in rows:
            self.assertAlmostEqual(weight, 1.0, places=10)
            self.assertTrue(positive)
            self.assertGreater(count, 0)

    def test_regional_weather_keys_are_unique_and_complete(self) -> None:
        path = ROOT / "data/processed/weather/regional/hourly/year=*/part.parquet"
        count, distinct_count, zones, nulls = self.connection.execute(f"""
          SELECT COUNT(*), COUNT(DISTINCT (timestamp_utc, weather_zone)),
            COUNT(DISTINCT weather_zone), COUNT(*) FILTER (WHERE temperature_mean_c IS NULL)
          FROM read_parquet('{path}')
        """).fetchone()
        self.assertEqual(count, 196488 * 9)
        self.assertEqual(distinct_count, count)
        self.assertEqual(zones, 9)
        self.assertEqual(nulls, 0)

    def test_integrated_alignment_and_known_incomplete_interval(self) -> None:
        path = ROOT / "data/processed/weather/regional/integrated/weather_demand_hourly.parquet"
        count, distinct_count, weather_nulls, demand_nulls = self.connection.execute(f"""
          SELECT COUNT(*), COUNT(DISTINCT (timestamp_utc, weather_zone)),
            COUNT(*) FILTER (WHERE temperature_mean_c IS NULL),
            COUNT(*) FILTER (WHERE demand_mw IS NULL)
          FROM read_parquet('{path}')
        """).fetchone()
        self.assertEqual(count, 196482 * 9)
        self.assertEqual(distinct_count, count)
        self.assertEqual(weather_nulls, 0)
        self.assertEqual(demand_nulls, 9)  # documented 2016-11-06 source interval


if __name__ == "__main__":
    unittest.main()
