from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from scripts.weather.seasonal import climate_normal_forecast, normalize_seas5_sample, validate_seasonal_netcdf
from scripts.discover_phase5a_cfsv2 import parse_catalog


def test_climate_normal_excludes_future_observations():
    months = pd.date_range("2004-01-01", "2024-12-01", freq="MS")
    history = pd.DataFrame({"month": months, "weather_zone": "SYSTEM", "temperature_mean_c": months.month})
    issue = pd.Timestamp("2020-01-01")
    expected = climate_normal_forecast(history, [issue], ["temperature_mean_c"], minimum_years=10)
    history.loc[history["month"] >= issue, "temperature_mean_c"] = 1_000_000
    actual = climate_normal_forecast(history, [issue], ["temperature_mean_c"], minimum_years=10)
    pd.testing.assert_frame_equal(expected, actual)
    assert expected["target_month"].min() > issue


def test_validate_seasonal_netcdf_preserves_member_and_lead(tmp_path: Path):
    path = tmp_path / "seasonal.nc"
    dataset = xr.Dataset(
        {"t2m": (("forecast_reference_time", "leadtime_month", "number", "latitude", "longitude"),
                 np.ones((1, 2, 3, 2, 2), dtype="float32"))},
        coords={"forecast_reference_time": [np.datetime64("2024-01-01")], "leadtime_month": [1, 2],
                "number": [0, 1, 2], "latitude": [36.0, 35.0], "longitude": [-100.0, -99.0]})
    dataset["t2m"].attrs["units"] = "K"
    dataset.to_netcdf(path)
    result = validate_seasonal_netcdf(path, {"t2m"})
    assert result["dimensions"]["number"] == 3
    assert result["dimensions"]["leadtime_month"] == 2
    assert result["units"]["t2m"] == "K"


def test_validate_ncep_indexing_time(tmp_path: Path):
    path = tmp_path / "ncep.nc"
    dataset = xr.Dataset(
        {"t2m": (("indexing_time", "forecastMonth", "number", "latitude", "longitude"),
                 np.ones((1, 2, 3, 2, 2), dtype="float32"))},
        coords={"indexing_time": [np.datetime64("2024-01-01")], "forecastMonth": [1, 2],
                "number": [0, 1, 2], "latitude": [36.0, 35.0], "longitude": [-100.0, -99.0]})
    dataset.to_netcdf(path)
    result = validate_seasonal_netcdf(path, {"t2m"})
    assert result["dimensions"]["indexing_time"] == 1


def test_normalize_seas5_units_and_target_month(tmp_path: Path):
    path = tmp_path / "sample.nc"
    dims = ("forecast_reference_time", "forecastMonth", "number", "latitude", "longitude")
    shape = (1, 2, 1, 1, 1)
    variables = {
        "t2m": (dims, np.full(shape, 293.15, dtype="float32")),
        "d2m": (dims, np.full(shape, 283.15, dtype="float32")),
        "u10": (dims, np.full(shape, 3.0, dtype="float32")),
        "v10": (dims, np.full(shape, 4.0, dtype="float32")),
        "tprate": (dims, np.full(shape, 1e-7, dtype="float32")),
        "msdsrf": (dims, np.full(shape, 100.0, dtype="float32")),
    }
    dataset = xr.Dataset(variables, coords={"forecast_reference_time": [np.datetime64("2024-01-01")],
                         "forecastMonth": [1, 2], "number": [0], "latitude": [30.0], "longitude": [-100.0]})
    dataset.to_netcdf(path)
    result = normalize_seas5_sample(path, "archived_realtime_forecast")
    assert result["target_month"].tolist() == [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01")]
    assert np.allclose(result["wind_speed_mps"], 5.0)
    assert np.allclose(result["temperature_mean_c"], 20.0)
    assert result["precipitation_mm"].iloc[0] > result["precipitation_mm"].iloc[1]
    assert result["available_at"].notna().all()


def test_hindcast_is_not_labeled_realtime_available(tmp_path: Path):
    path = tmp_path / "hindcast.nc"
    dims = ("forecast_reference_time", "forecastMonth", "number", "latitude", "longitude")
    shape = (1, 1, 1, 1, 1)
    variables = {name: (dims, np.ones(shape, dtype="float32"))
                 for name in ("t2m", "d2m", "u10", "v10", "tprate", "msdsrf")}
    variables["t2m"] = (dims, np.full(shape, 280.0, dtype="float32"))
    variables["d2m"] = (dims, np.full(shape, 275.0, dtype="float32"))
    xr.Dataset(variables, coords={"forecast_reference_time": [np.datetime64("2016-01-01")],
               "forecastMonth": [1], "number": [0], "latitude": [30.0], "longitude": [-100.0]}).to_netcdf(path)
    result = normalize_seas5_sample(path, "retrospective_hindcast")
    assert result["available_at"].isna().all()
    assert set(result["availability_kind"]) == {"retrospective_not_realtime_archive"}


def test_thredds_catalog_ref_and_dataset_parsing():
    payload = b'''<catalog xmlns="http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0"
      xmlns:xlink="http://www.w3.org/1999/xlink">
      <catalogRef xlink:href="day/catalog.xml" xlink:title="day" name="day"/>
      <dataset name="sample.grb2" urlPath="model/sample.grb2"/>
    </catalog>'''
    artifacts, references = parse_catalog(payload, "https://example.test/root/catalog.xml")
    assert artifacts == [{"name": "sample.grb2", "url_path": "model/sample.grb2"}]
    assert references == ["https://example.test/root/day/catalog.xml"]
