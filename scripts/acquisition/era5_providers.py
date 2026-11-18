"""Cloud ERA5 provider adapters with a shared canonical subset interface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import icechunk
import numpy as np
import xarray as xr
import yaml


CANONICAL_VARIABLES = ("t2m", "d2m", "u10", "v10", "tp", "ssrd")
LONG_TO_SHORT = {
    "2m_temperature": "t2m",
    "2m_dewpoint_temperature": "d2m",
    "10m_u_component_of_wind": "u10",
    "10m_v_component_of_wind": "v10",
    "total_precipitation": "tp",
    "surface_solar_radiation_downwards": "ssrd",
}


@dataclass(frozen=True)
class Era5SubsetRequest:
    timestamps: tuple[np.datetime64, ...]
    north: float
    west: float
    south: float
    east: float


class Era5Provider(Protocol):
    name: str
    requires_authentication: bool

    def open(self) -> xr.Dataset: ...

    def provenance(self) -> dict[str, Any]: ...


def canonical_subset(source: xr.Dataset, request: Era5SubsetRequest) -> xr.Dataset:
    time_name = "valid_time" if "valid_time" in source.coords else "time"
    names = {
        canonical: canonical if canonical in source.data_vars else long_name
        for long_name, canonical in LONG_TO_SHORT.items()
    }
    missing = [canonical for canonical, actual in names.items() if actual not in source.data_vars]
    if missing:
        raise ValueError(f"provider is missing ERA5 variables: {missing}")
    longitude = source.longitude
    if float(longitude.min()) >= 0.0:
        west, east = request.west % 360.0, request.east % 360.0
    else:
        west, east = request.west, request.east
    latitude_slice = (
        slice(request.north, request.south)
        if float(source.latitude[0]) > float(source.latitude[-1])
        else slice(request.south, request.north)
    )
    subset = source[list(names.values())].sel(
        {
            time_name: np.asarray(request.timestamps, dtype="datetime64[ns]"),
            "latitude": latitude_slice,
            "longitude": slice(west, east),
        }
    )
    rename = {actual: canonical for canonical, actual in names.items() if actual != canonical}
    if time_name != "valid_time":
        rename[time_name] = "valid_time"
    return subset.rename(rename)


class AwsEarthmoverProvider:
    name = "aws_earthmover"
    requires_authentication = False

    def __init__(self) -> None:
        storage = icechunk.s3_storage(
            bucket="earthmover-icechunk-era5",
            prefix="icechunkV2",
            region="us-east-1",
            anonymous=True,
        )
        repository = icechunk.Repository.open(storage)
        self.session = repository.readonly_session("main")

    def open(self) -> xr.Dataset:
        return xr.open_zarr(
            self.session.store, group="single/temporal", consolidated=False, chunks=None
        )

    def provenance(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "bucket": "earthmover-icechunk-era5",
            "prefix": "icechunkV2",
            "group": "single/temporal",
            "snapshot": str(self.session.snapshot_id),
        }


class GoogleArcoProvider:
    name = "google_arco"
    requires_authentication = False
    url = (
        "https://storage.googleapis.com/gcp-public-data-arco-era5/ar/"
        "full_37-1h-0p25deg-chunk-1.zarr-v3"
    )

    def open(self) -> xr.Dataset:
        return xr.open_zarr(self.url, chunks=None)

    def provenance(self) -> dict[str, Any]:
        source = self.open()
        return {
            "provider": self.name,
            "url": self.url,
            "valid_time_start": str(source.attrs.get("valid_time_start")),
            "valid_time_stop": str(source.attrs.get("valid_time_stop")),
            "last_updated": str(source.attrs.get("last_updated")),
        }


class CopernicusArcoProvider:
    name = "copernicus_arco"
    requires_authentication = True
    url = (
        "https://arco.datastores.ecmwf.int/cadl-arco-time-002/arco/"
        "reanalysis_era5_single_levels/sfc/timeChunked.zarr"
    )

    def __init__(self, config_path: Path | None = None) -> None:
        path = config_path or Path.home() / ".cdsapirc"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        key = config.get("key") if isinstance(config, dict) else None
        if not key:
            raise ValueError("CDS API key is missing from local configuration")
        self._storage_options = {"headers": {"Authorization": f"Bearer {key}"}}

    def open(self) -> xr.Dataset:
        return xr.open_zarr(
            self.url,
            consolidated=True,
            chunks=None,
            storage_options=self._storage_options,
        )

    def provenance(self) -> dict[str, Any]:
        source = self.open()
        time = source["valid_time"] if "valid_time" in source.coords else source["time"]
        return {
            "provider": self.name,
            "url": self.url,
            "valid_time_start": str(time.values[0]),
            "valid_time_stop": str(time.values[-1]),
        }
