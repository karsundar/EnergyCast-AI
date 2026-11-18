import sys
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from weather.era5 import (  # noqa: E402
    aggregate_daily_grid,
    expected_local_day_hours,
    extract_netcdf_bundle_atomic,
    normalize_hourly,
    validate_era5_dataset,
)
from acquisition.era5_providers import Era5SubsetRequest, canonical_subset  # noqa: E402


def _datasets(times=None):
    timestamps = pd.DatetimeIndex(times or ["2024-01-01T00:00:00", "2024-01-01T01:00:00"])
    coords = {"valid_time": timestamps, "latitude": [36.5, 36.25], "longitude": [-106.75, -106.5]}
    shape = (len(timestamps), 2, 2)
    instant = xr.Dataset(
        {
            "t2m": (("valid_time", "latitude", "longitude"), np.full(shape, 293.15, dtype="float32")),
            "d2m": (("valid_time", "latitude", "longitude"), np.full(shape, 283.15, dtype="float32")),
            "u10": (("valid_time", "latitude", "longitude"), np.full(shape, 3.0, dtype="float32")),
            "v10": (("valid_time", "latitude", "longitude"), np.full(shape, 4.0, dtype="float32")),
        }, coords=coords,
    )
    accum = xr.Dataset(
        {
            "tp": (("valid_time", "latitude", "longitude"), np.full(shape, 0.001, dtype="float32")),
            "ssrd": (("valid_time", "latitude", "longitude"), np.full(shape, 3_600_000.0, dtype="float32")),
        }, coords=coords,
    )
    for name in instant.data_vars:
        instant[name].attrs["GRIB_stepType"] = "instant"
    instant.t2m.attrs["units"] = instant.d2m.attrs["units"] = "K"
    instant.u10.attrs["units"] = instant.v10.attrs["units"] = "m s**-1"
    accum.tp.attrs.update(units="m", GRIB_stepType="accum")
    accum.ssrd.attrs.update(units="J m**-2", GRIB_stepType="accum")
    return instant, accum


class Era5WeatherTest(unittest.TestCase):
    def test_provider_subset_canonicalizes_names_time_and_longitude(self):
        times = pd.DatetimeIndex(["2024-01-01T00:00:00", "2024-01-01T06:00:00"])
        latitude = [36.5, 36.25]
        longitude = [253.25, 253.5]
        shape = (2, 2, 2)
        long_names = {
            "2m_temperature": "t2m",
            "2m_dewpoint_temperature": "d2m",
            "10m_u_component_of_wind": "u10",
            "10m_v_component_of_wind": "v10",
            "total_precipitation": "tp",
            "surface_solar_radiation_downwards": "ssrd",
        }
        source = xr.Dataset(
            {
                name: (("time", "latitude", "longitude"), np.ones(shape, dtype="float32"))
                for name in long_names
            },
            coords={"time": times, "latitude": latitude, "longitude": longitude},
        )
        request = Era5SubsetRequest(
            tuple(times.values), 36.5, -106.75, 36.25, -106.5
        )
        result = canonical_subset(source, request)
        self.assertEqual(set(result.data_vars), set(long_names.values()))
        self.assertIn("valid_time", result.coords)
        self.assertEqual(result.sizes, {"valid_time": 2, "latitude": 2, "longitude": 2})

    def test_netcdf_parsing_coordinates_and_timestamps(self):
        instant, _ = _datasets()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instant.nc"
            instant.to_netcdf(path, engine="h5netcdf")
            result = validate_era5_dataset(path, {"t2m", "d2m", "u10", "v10"})
        self.assertEqual(result["dimensions"], {"valid_time": 2, "latitude": 2, "longitude": 2})
        self.assertEqual(result["timestamp_spacing_hours"], [1.0])
        self.assertEqual(result["resolution_degrees"], [0.25, 0.25])
        self.assertEqual(sum(result["missing_values"].values()), 0)

    def test_coordinate_validation_rejects_duplicates(self):
        instant, _ = _datasets()
        instant = instant.assign_coords(longitude=[-106.75, -106.75])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.nc"
            instant.to_netcdf(path, engine="h5netcdf")
            with self.assertRaisesRegex(ValueError, "duplicates"):
                validate_era5_dataset(path)

    def test_multi_file_zip_extraction_and_traversal_rejection(self):
        instant, accum = _datasets()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            instant_path, accum_path = root / "instant.nc", root / "accum.nc"
            instant.to_netcdf(instant_path, engine="h5netcdf")
            accum.to_netcdf(accum_path, engine="h5netcdf")
            archive = root / "bundle.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.write(instant_path, "instant.nc")
                output.write(accum_path, "accum.nc")
            records = extract_netcdf_bundle_atomic(archive, root / "extracted")
            self.assertEqual({item["name"] for item in records}, {"instant.nc", "accum.nc"})
            self.assertTrue(all(len(item["sha256"]) == 64 for item in records))
            bad = root / "bad.zip"
            with zipfile.ZipFile(bad, "w") as output:
                output.writestr("../escape.nc", b"CDF\x01" + b"\x00" * 8)
            with self.assertRaisesRegex(ValueError, "unsafe"):
                extract_netcdf_bundle_atomic(bad, root / "bad-output")

    def test_unit_conversions_do_not_difference_accumulations(self):
        instant, accum = _datasets()
        result = normalize_hourly(instant, accum)
        self.assertAlmostEqual(float(result.temperature_c.mean()), 20.0, places=4)
        self.assertAlmostEqual(float(result.temperature_f.mean()), 68.0, places=4)
        self.assertAlmostEqual(float(result.wind_speed_mps.mean()), 5.0, places=4)
        self.assertAlmostEqual(float(result.precipitation_mm.mean()), 1.0, places=4)
        self.assertAlmostEqual(float(result.solar_radiation_mj_m2.mean()), 3.6, places=4)
        self.assertAlmostEqual(float(result.solar_mean_flux_w_m2.mean()), 1000.0, places=4)
        self.assertTrue(bool(((result.relative_humidity_pct >= 0) & (result.relative_humidity_pct <= 100)).all()))

    def test_dst_expected_hours_and_grid_cell_degree_days(self):
        self.assertEqual(expected_local_day_hours(date(2024, 3, 10)), 23)
        self.assertEqual(expected_local_day_hours(date(2024, 11, 3)), 25)
        instant, accum = _datasets(["2024-01-01T06:00:00", "2024-01-01T07:00:00"])
        hourly = normalize_hourly(instant, accum)
        daily = aggregate_daily_grid(hourly)
        self.assertAlmostEqual(float(daily.cdd_65f.mean()), 3.0, places=4)
        self.assertAlmostEqual(float(daily.hdd_65f.mean()), 0.0, places=4)
        self.assertEqual(int(daily.expected_observation_count[0]), 24)
        self.assertAlmostEqual(float(daily.completeness_pct.mean()), 2 / 24 * 100, places=4)


if __name__ == "__main__":
    unittest.main()
