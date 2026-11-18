"""Leakage-safe utilities for seasonal weather forecasts and climate normals."""

from __future__ import annotations

import calendar
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr


DIMENSION_ALIASES = {
    "issue_date": ("forecast_reference_time", "indexing_time", "time"),
    "lead": ("leadtime_month", "forecastMonth", "step"),
    "member": ("number", "member", "realization"),
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lon"),
}


def validate_seasonal_netcdf(path: Path, expected_variables: set[str] | None = None) -> dict[str, object]:
    """Validate structure while preserving member and lead dimensions."""
    with xr.open_dataset(path) as ds:
        dimensions = set(ds.dims) | set(ds.coords)
        found = {key: next((name for name in aliases if name in dimensions), None)
                 for key, aliases in DIMENSION_ALIASES.items()}
        missing_dims = [key for key, value in found.items() if value is None]
        if missing_dims:
            raise ValueError(f"missing seasonal dimensions: {missing_dims}")
        if expected_variables and not expected_variables.intersection(ds.data_vars):
            raise ValueError("none of the requested seasonal variables are present")
        if not ds.data_vars:
            raise ValueError("seasonal NetCDF contains no variables")
        return {
            "dimensions": {name: int(size) for name, size in ds.sizes.items()},
            "variables": sorted(ds.data_vars),
            "coordinates": sorted(ds.coords),
            "units": {name: str(ds[name].attrs.get("units", "")) for name in ds.data_vars},
        }


def climate_normal_forecast(
    history: pd.DataFrame,
    issue_dates: list[pd.Timestamp],
    value_columns: list[str],
    geography_column: str = "weather_zone",
    date_column: str = "month",
    horizons: int = 12,
    minimum_years: int = 10,
) -> pd.DataFrame:
    """Forecast calendar-month normals using only observations before each issue date."""
    source = history.copy()
    source[date_column] = (pd.to_datetime(source[date_column], utc=True).dt.tz_localize(None)
                           .dt.to_period("M").dt.to_timestamp())
    records: list[dict[str, object]] = []
    for issue in issue_dates:
        issue = pd.Timestamp(issue).to_period("M").to_timestamp()
        available = source[source[date_column] < issue]
        for geography, geo in available.groupby(geography_column, observed=True):
            for lead in range(1, horizons + 1):
                target = issue + pd.DateOffset(months=lead)
                matching = geo[geo[date_column].dt.month == target.month]
                if matching[date_column].dt.year.nunique() < minimum_years:
                    continue
                row: dict[str, object] = {
                    "issue_date": issue,
                    "target_month": target,
                    "lead_month": lead,
                    geography_column: geography,
                    "forecast_source": "climate_normal",
                    "forecast_kind": "baseline",
                    "ensemble_member": np.nan,
                    "available_at": issue,
                }
                row.update({column: float(matching[column].mean()) for column in value_columns})
                records.append(row)
    return pd.DataFrame(records)


def expected_days(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def relative_humidity_pct(temperature_k: np.ndarray, dewpoint_k: np.ndarray) -> np.ndarray:
    """Magnus approximation over water, bounded to physical percentage limits."""
    temperature_c = temperature_k - 273.15
    dewpoint_c = dewpoint_k - 273.15
    ratio = np.exp((17.625 * dewpoint_c) / (243.04 + dewpoint_c)
                   - (17.625 * temperature_c) / (243.04 + temperature_c))
    return np.clip(100.0 * ratio, 0.0, 100.0)


def normalize_seas5_sample(path: Path, forecast_kind: str, provider: str = "copernicus_seas5") -> pd.DataFrame:
    """Normalize one monthly SEAS5 member/lead sample without spatial aggregation."""
    if forecast_kind not in {"archived_realtime_forecast", "retrospective_hindcast"}:
        raise ValueError(f"unsupported forecast_kind: {forecast_kind}")
    with xr.open_dataset(path) as ds:
        required = {"t2m", "d2m", "u10", "v10", "tprate", "msdsrf"}
        missing = required - set(ds.data_vars)
        if missing:
            raise ValueError(f"missing SEAS5 variables: {sorted(missing)}")
        frame = ds[list(sorted(required))].to_dataframe().reset_index()
    issue_column = next((name for name in ("forecast_reference_time", "indexing_time", "time")
                         if name in frame.columns), None)
    if issue_column is None:
        raise ValueError("missing forecast initialization coordinate")
    frame = frame.rename(columns={issue_column: "issue_date", "forecastMonth": "lead_month",
                                  "number": "ensemble_member"})
    frame["issue_date"] = pd.to_datetime(frame["issue_date"], utc=True)
    frame["lead_month"] = frame["lead_month"].astype("int16")
    frame["ensemble_member"] = frame["ensemble_member"].astype("int16")
    # CDS forecastMonth=1 is the initialization calendar month.
    frame["target_month"] = [
        (issue.tz_localize(None).to_period("M") + int(lead) - 1).to_timestamp()
        for issue, lead in zip(frame["issue_date"], frame["lead_month"], strict=True)
    ]
    release_offset = pd.Timedelta(days=9, hours=12) if provider == "copernicus_cfsv2" else pd.Timedelta(days=5, hours=12)
    if forecast_kind == "retrospective_hindcast":
        frame["available_at"] = pd.NaT
        frame["availability_kind"] = "retrospective_not_realtime_archive"
    else:
        frame["available_at"] = frame["issue_date"] + release_offset
        frame["availability_kind"] = "scheduled_operational_release"
    frame["temperature_mean_c"] = frame["t2m"] - 273.15
    frame["temperature_mean_f"] = frame["temperature_mean_c"] * 9.0 / 5.0 + 32.0
    frame["dewpoint_mean_c"] = frame["d2m"] - 273.15
    frame["relative_humidity_pct"] = relative_humidity_pct(frame["t2m"].to_numpy(), frame["d2m"].to_numpy())
    frame["wind_speed_mps"] = np.hypot(frame["u10"], frame["v10"])
    seconds = np.array([expected_days(date.year, date.month) * 86400 for date in frame["target_month"]])
    frame["precipitation_mm"] = frame["tprate"] * seconds * 1000.0
    frame["solar_energy_mj_m2"] = frame["msdsrf"] * seconds / 1_000_000.0
    frame["forecast_kind"] = forecast_kind
    frame["source_provider"] = "copernicus_cds"
    frame["forecast_system"] = "ncep_cfsv2_2" if provider == "copernicus_cfsv2" else "ecmwf_seas5_51"
    frame["source_file"] = path.name
    columns = [
        "issue_date", "available_at", "target_month", "lead_month", "ensemble_member",
        "latitude", "longitude", "temperature_mean_c", "temperature_mean_f", "dewpoint_mean_c",
        "relative_humidity_pct", "wind_speed_mps", "precipitation_mm", "solar_energy_mj_m2",
        "t2m", "d2m", "u10", "v10", "tprate", "msdsrf", "forecast_kind", "source_provider",
        "forecast_system", "availability_kind", "source_file",
    ]
    result = frame[columns]
    if result.duplicated(["issue_date", "lead_month", "ensemble_member", "latitude", "longitude"]).any():
        raise ValueError("duplicate SEAS5 member/grid records")
    if not np.isfinite(result.select_dtypes(include=["number"]).to_numpy()).all():
        raise ValueError("nonfinite normalized SEAS5 values")
    return result
