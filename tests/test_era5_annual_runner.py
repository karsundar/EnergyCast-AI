from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "run_era5_aws_annual", ROOT / "scripts/run_era5_aws_annual.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class Era5AnnualRunnerTests(unittest.TestCase):
    def test_bounded_month_normalization_and_cache_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            annual = root / "era5_texas_2024.nc"
            processed = root / "processed"
            times = pd.date_range("2024-01-01", "2024-02-01 06:00", freq="h")
            latitude = np.array([36.5, 36.25], dtype=np.float32)
            longitude = np.array([253.25, 253.5], dtype=np.float32)
            shape = (len(times), len(latitude), len(longitude))
            hour = np.arange(len(times), dtype=np.float32)[:, None, None]
            base = np.broadcast_to(hour, shape)
            dataset = xr.Dataset(
                {
                    "t2m": (("valid_time", "latitude", "longitude"), 280 + base * 0.01),
                    "d2m": (("valid_time", "latitude", "longitude"), 275 + base * 0.01),
                    "u10": (("valid_time", "latitude", "longitude"), np.ones(shape, dtype=np.float32) * 3),
                    "v10": (("valid_time", "latitude", "longitude"), np.ones(shape, dtype=np.float32) * 4),
                    "tp": (("valid_time", "latitude", "longitude"), np.ones(shape, dtype=np.float32) * 0.001),
                    "ssrd": (("valid_time", "latitude", "longitude"), np.ones(shape, dtype=np.float32) * 3600),
                },
                coords={"valid_time": times, "latitude": latitude, "longitude": longitude},
            )
            dataset.to_netcdf(annual, engine="h5netcdf")

            result = MODULE.normalize_month_from_annual(
                annual, None, processed, 2024, 1, expected_grid=(2, 2)
            )
            self.assertEqual(result["status"], "validated")
            self.assertEqual(result["expected_hours"], 744)
            self.assertEqual(result["outputs"]["hourly"]["rows"], 744 * 4)
            self.assertEqual(result["outputs"]["daily"]["rows"], 31 * 4)
            self.assertEqual(result["outputs"]["monthly"]["rows"], 4)
            self.assertFalse(result["cache_hit"])

            cached = MODULE.normalize_month_from_annual(
                annual, None, processed, 2024, 1, expected_grid=(2, 2)
            )
            self.assertTrue(cached["cache_hit"])

    def test_request_fingerprint_rejects_wrong_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "annual.nc"
            sidecar = root / "annual.request.json"
            path.write_bytes(b"not-used-because-fingerprint-fails")
            sidecar.write_text(json.dumps({"request_sha256": "wrong"}))
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                MODULE.validate_annual_file(
                    path, sidecar, MODULE.year_request(2004), expected_grid=(2, 2)
                )

    def test_disk_space_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            free = MODULE.ensure_free_space(Path(temporary), 0.0)
            self.assertGreater(free, 0)


if __name__ == "__main__":
    unittest.main()
