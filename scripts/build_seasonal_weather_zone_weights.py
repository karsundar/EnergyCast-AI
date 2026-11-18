#!/usr/bin/env python3
"""Build approximate ERCOT area weights for the validated 1-degree seasonal grid."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
GEO = ROOT / "data" / "processed" / "weather" / "geospatial"


def main() -> int:
    zones = gpd.read_parquet(GEO / "ercot_weather_zones_approximate.parquet")
    sample = pd.read_parquet(
        ROOT / "data" / "processed" / "weather" / "seasonal" / "seas5_forecast_sample.parquet",
        columns=["latitude", "longitude"],
    ).drop_duplicates()
    cells = gpd.GeoDataFrame(
        sample,
        geometry=[box(lon - .5, lat - .5, lon + .5, lat + .5)
                  for lat, lon in zip(sample.latitude, sample.longitude, strict=True)],
        crs="EPSG:4326",
    )
    equal_area = "EPSG:5070"
    cells_ea = cells.to_crs(equal_area)
    zones_ea = zones.to_crs(equal_area)
    intersections = gpd.overlay(cells_ea, zones_ea[["weather_zone", "geometry"]],
                                how="intersection", keep_geom_type=False)
    intersections["intersection_area_m2"] = intersections.geometry.area
    areas = cells_ea.set_index(["latitude", "longitude"]).geometry.area
    intersections["grid_cell_area_m2"] = [areas.loc[(lat, lon)] for lat, lon in
                                            zip(intersections.latitude, intersections.longitude, strict=True)]
    intersections["grid_cell_fraction"] = intersections.intersection_area_m2 / intersections.grid_cell_area_m2
    intersections["zone_weight"] = (intersections.intersection_area_m2
                                     / intersections.groupby("weather_zone").intersection_area_m2.transform("sum"))
    matrix = intersections[["weather_zone", "latitude", "longitude", "intersection_area_m2",
                            "grid_cell_area_m2", "grid_cell_fraction", "zone_weight"]].copy()
    system = matrix.groupby(["latitude", "longitude"], as_index=False).agg(
        intersection_area_m2=("intersection_area_m2", "sum"),
        grid_cell_area_m2=("grid_cell_area_m2", "first"),
        grid_cell_fraction=("grid_cell_fraction", "sum"),
    )
    system["weather_zone"] = "SYSTEM"
    system["zone_weight"] = system.intersection_area_m2 / system.intersection_area_m2.sum()
    matrix = pd.concat([matrix, system[matrix.columns]], ignore_index=True)
    destination = GEO / "ercot_seasonal_1deg_grid_weights.parquet"
    matrix.to_parquet(destination, index=False, compression="zstd")
    summary = {
        "status": "validated_approximate", "source_grid_resolution_degrees": 1.0,
        "grid_cells": int(sample.shape[0]),
        "intersecting_cells": int(matrix[["latitude", "longitude"]].drop_duplicates().shape[0]),
        "weight_rows": len(matrix), "crs_area": equal_area,
        "zone_weight_sums": matrix.groupby("weather_zone").zone_weight.sum().to_dict(),
        "mapping_approximate": True,
    }
    (GEO / "seasonal_mapping_validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
