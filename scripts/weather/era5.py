"""ERA5 validation and leakage-safe weather feature transformations."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
import zipfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import xarray as xr


NETCDF_SIGNATURES = (b"CDF\x01", b"CDF\x02", b"\x89HDF\r\n\x1a\n")
REQUIRED_COORDINATES = ("valid_time", "latitude", "longitude")
ERA5_VARIABLES = {"t2m", "d2m", "u10", "v10", "tp", "ssrd"}
TEXAS_TIMEZONE = "America/Chicago"
SECONDS_PER_HOUR = 3600.0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def request_fingerprint(request: dict[str, Any]) -> str:
    payload = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_member_path(root: Path, member_name: str) -> Path:
    member = Path(member_name)
    if member.is_absolute() or ".." in member.parts or not member.name:
        raise ValueError(f"unsafe archive member: {member_name}")
    destination = (root / member).resolve()
    if root.resolve() not in destination.parents:
        raise ValueError(f"archive member escapes destination: {member_name}")
    return destination


def extract_netcdf_bundle_atomic(archive_path: Path, destination: Path) -> list[dict[str, Any]]:
    """Validate and atomically extract every NetCDF member from a CDS ZIP."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        records: list[dict[str, Any]] = []
        with zipfile.ZipFile(archive_path) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if not members or archive.testzip() is not None:
                raise ValueError("NetCDF ZIP is empty or corrupt")
            for member in members:
                target = _safe_member_path(temporary, member.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink, length=1024 * 1024)
                with target.open("rb") as stream:
                    signature = stream.read(8)
                if not any(signature.startswith(value) for value in NETCDF_SIGNATURES):
                    raise ValueError(f"invalid NetCDF member signature: {member.filename}")
                records.append({
                    "name": member.filename,
                    "path": str(destination / Path(member.filename)),
                    "compressed_bytes": member.compress_size,
                    "bytes": member.file_size,
                    "compression_ratio": member.compress_size / member.file_size if member.file_size else None,
                    "sha256": sha256_file(target),
                })
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(temporary, destination)
        return records
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def validate_era5_dataset(path: Path, expected_variables: Iterable[str] | None = None) -> dict[str, Any]:
    """Parse one ERA5 NetCDF member and validate its coordinates and timestamps."""
    with xr.open_dataset(path, engine="h5netcdf") as dataset:
        missing_coords = [name for name in REQUIRED_COORDINATES if name not in dataset.coords]
        if missing_coords:
            raise ValueError(f"missing ERA5 coordinates: {', '.join(missing_coords)}")
        variables = set(dataset.data_vars)
        expected = set(expected_variables or ())
        if expected and not expected.issubset(variables):
            raise ValueError(f"missing ERA5 variables: {', '.join(sorted(expected - variables))}")
        timestamps = pd.DatetimeIndex(dataset.valid_time.values)
        if timestamps.has_duplicates or not timestamps.is_monotonic_increasing:
            raise ValueError("ERA5 timestamps must be unique and increasing")
        latitudes = np.asarray(dataset.latitude.values)
        longitudes = np.asarray(dataset.longitude.values)
        if np.unique(latitudes).size != latitudes.size or np.unique(longitudes).size != longitudes.size:
            raise ValueError("ERA5 spatial coordinates contain duplicates")
        if not np.all(np.diff(latitudes) < 0) or not np.all(np.diff(longitudes) > 0):
            raise ValueError("unexpected ERA5 coordinate ordering")
        nulls = {name: int(dataset[name].isnull().sum().compute()) for name in dataset.data_vars}
        return {
            "variables": sorted(variables),
            "dimensions": dict(dataset.sizes),
            "start": timestamps.min().isoformat(),
            "end": timestamps.max().isoformat(),
            "timestamp_count": len(timestamps),
            "timestamp_spacing_hours": [float(value / pd.Timedelta(hours=1)) for value in timestamps.to_series().diff().dropna().unique()],
            "latitude": [float(latitudes.min()), float(latitudes.max())],
            "longitude": [float(longitudes.min()), float(longitudes.max())],
            "resolution_degrees": [float(abs(np.diff(latitudes).mean())), float(np.diff(longitudes).mean())],
            "missing_values": nulls,
            "global_attributes": {key: _json_value(value) for key, value in dataset.attrs.items()},
            "variable_metadata": {
                name: {
                    "dtype": str(dataset[name].dtype),
                    "dimensions": list(dataset[name].dims),
                    "units": dataset[name].attrs.get("units"),
                    "step_type": dataset[name].attrs.get("GRIB_stepType"),
                    "attributes": {key: _json_value(value) for key, value in dataset[name].attrs.items()},
                }
                for name in dataset.data_vars
            },
        }


def _json_value(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def kelvin_to_celsius(values: xr.DataArray) -> xr.DataArray:
    return values - 273.15


def celsius_to_fahrenheit(values: xr.DataArray) -> xr.DataArray:
    return values * 9.0 / 5.0 + 32.0


def relative_humidity_percent(temperature_c: xr.DataArray, dewpoint_c: xr.DataArray) -> xr.DataArray:
    numerator = np.exp((17.625 * dewpoint_c) / (243.04 + dewpoint_c))
    denominator = np.exp((17.625 * temperature_c) / (243.04 + temperature_c))
    return (100.0 * numerator / denominator).clip(min=0.0, max=100.0)


def expected_local_day_hours(day: date, timezone_name: str = TEXAS_TIMEZONE) -> int:
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(day, time.min, zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
    return int((end - start).total_seconds() / SECONDS_PER_HOUR)


def normalize_hourly(instantaneous: xr.Dataset, accumulated: xr.Dataset) -> xr.Dataset:
    """Convert units and derive grid-cell features without changing hourly accumulations."""
    required_instant = {"t2m", "d2m", "u10", "v10"}
    required_accum = {"tp", "ssrd"}
    if not required_instant.issubset(instantaneous.data_vars):
        raise ValueError(f"missing instantaneous variables: {sorted(required_instant - set(instantaneous.data_vars))}")
    if not required_accum.issubset(accumulated.data_vars):
        raise ValueError(f"missing accumulated variables: {sorted(required_accum - set(accumulated.data_vars))}")
    instant, accum = xr.align(instantaneous, accumulated, join="exact")
    temperature_c = kelvin_to_celsius(instant.t2m)
    dewpoint_c = kelvin_to_celsius(instant.d2m)
    result = xr.Dataset({
        "temperature_c": temperature_c,
        "temperature_f": celsius_to_fahrenheit(temperature_c),
        "dewpoint_c": dewpoint_c,
        "relative_humidity_pct": relative_humidity_percent(temperature_c, dewpoint_c),
        "wind_u_mps": instant.u10,
        "wind_v_mps": instant.v10,
        "wind_speed_mps": np.hypot(instant.u10, instant.v10),
        "precipitation_mm": accum.tp * 1000.0,
        "solar_radiation_j_m2": accum.ssrd,
        "solar_radiation_mj_m2": accum.ssrd / 1_000_000.0,
        "solar_mean_flux_w_m2": accum.ssrd / SECONDS_PER_HOUR,
    })
    result.attrs.update({
        "timezone": TEXAS_TIMEZONE,
        "accumulation_semantics": "ERA5 reanalysis values are one-hour accumulations ending at valid_time; no differencing applied",
        "hdd_cdd_base_f": 65.0,
    })
    for name, units in {
        "temperature_c": "degC", "temperature_f": "degF", "dewpoint_c": "degC",
        "relative_humidity_pct": "%", "wind_u_mps": "m s-1", "wind_v_mps": "m s-1",
        "wind_speed_mps": "m s-1", "precipitation_mm": "mm",
        "solar_radiation_j_m2": "J m-2", "solar_radiation_mj_m2": "MJ m-2",
        "solar_mean_flux_w_m2": "W m-2",
    }.items():
        result[name].attrs["units"] = units
    return result


def _local_dates(times: pd.DatetimeIndex, *, accumulated: bool) -> np.ndarray:
    utc = times.tz_localize("UTC") if times.tz is None else times.tz_convert("UTC")
    if accumulated:
        utc = utc - pd.Timedelta(nanoseconds=1)
    return utc.tz_convert(TEXAS_TIMEZONE).date.astype("datetime64[D]")


def aggregate_daily_grid(hourly: xr.Dataset) -> xr.Dataset:
    """Aggregate hourly grid cells to Texas-local days; HDD/CDD precede spatial aggregation."""
    times = pd.DatetimeIndex(hourly.valid_time.values)
    instant_dates = xr.DataArray(_local_dates(times, accumulated=False), dims="valid_time", name="local_date")
    accum_dates = xr.DataArray(_local_dates(times, accumulated=True), dims="valid_time", name="local_date")
    instant = hourly[["temperature_c", "temperature_f", "dewpoint_c", "relative_humidity_pct", "wind_speed_mps"]]
    daily = xr.Dataset({
        "temperature_mean_c": instant.temperature_c.groupby(instant_dates).mean("valid_time"),
        "temperature_min_c": instant.temperature_c.groupby(instant_dates).min("valid_time"),
        "temperature_max_c": instant.temperature_c.groupby(instant_dates).max("valid_time"),
        "temperature_mean_f": instant.temperature_f.groupby(instant_dates).mean("valid_time"),
        "relative_humidity_mean_pct": instant.relative_humidity_pct.groupby(instant_dates).mean("valid_time"),
        "wind_speed_mean_mps": instant.wind_speed_mps.groupby(instant_dates).mean("valid_time"),
        "precipitation_total_mm": hourly.precipitation_mm.groupby(accum_dates).sum("valid_time"),
        "solar_energy_total_mj_m2": hourly.solar_radiation_mj_m2.groupby(accum_dates).sum("valid_time"),
    })
    daily["hdd_65f"] = ((65.0 - daily.temperature_mean_f).clip(min=0.0))
    daily["cdd_65f"] = ((daily.temperature_mean_f - 65.0).clip(min=0.0))
    observation_count = instant.temperature_c.groupby(instant_dates).count("valid_time")
    expected = xr.DataArray(
        [expected_local_day_hours(pd.Timestamp(value).date()) for value in daily.local_date.values],
        dims="local_date", coords={"local_date": daily.local_date},
    )
    daily["observation_count"] = observation_count
    daily["expected_observation_count"] = expected
    daily["completeness_pct"] = observation_count / expected * 100.0
    daily.attrs.update(hourly.attrs)
    daily.attrs["degree_day_method"] = "grid-cell daily mean temperature, base 65 degF, before spatial aggregation"
    return daily
