#!/usr/bin/env python3
"""Build an explicitly approximate ERCOT eight-zone spatial weight matrix."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/geospatial"
OUT = ROOT / "data/processed/weather/geospatial"
REPORT = ROOT / "reports/phase3b_weather_zone_mapping.md"
ZONES = ("COAST", "EAST", "FAR_WEST", "NORTH", "NORTH_CENTRAL", "SOUTHERN", "SOUTH_CENTRAL", "WEST")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    crosswalk_path = RAW / "county_region_all8.csv"
    census_path = RAW / "cb_2024_us_county_500k.zip"
    crosswalk = pd.read_csv(crosswalk_path)
    corrections = {"Houtson": "Houston"}
    crosswalk["County"] = crosswalk.County.replace(corrections)
    crosswalk["Region"] = crosswalk.Region.replace({"SOUTH": "SOUTHERN"})
    if set(crosswalk.Region) != set(ZONES) or crosswalk.County.duplicated().any():
        raise ValueError("invalid eight-zone county crosswalk")

    counties = gpd.read_file(f"zip://{census_path}")
    counties = counties[counties.STATEFP == "48"][["GEOID", "NAME", "geometry"]].copy()
    counties = counties.merge(crosswalk, left_on="NAME", right_on="County", how="inner", validate="one_to_one")
    if len(counties) != len(crosswalk):
        missing = sorted(set(crosswalk.County) - set(counties.NAME))
        raise ValueError(f"crosswalk counties missing from Census geometry: {missing}")
    counties["mapping_authority"] = "secondary_academic_replication"
    counties["mapping_approximate"] = True
    counties.to_parquet(OUT / "ercot_county_weather_zones.parquet", index=False)

    zones = counties.dissolve(by="Region", as_index=False, aggfunc={"GEOID": "count"})
    zones = zones.rename(columns={"Region": "weather_zone", "GEOID": "county_count"})
    zones["mapping_authority"] = "secondary_academic_replication"
    zones["mapping_approximate"] = True
    zones.to_parquet(OUT / "ercot_weather_zones_approximate.parquet", index=False)

    sample = pd.read_parquet(
        ROOT / "data/processed/weather/hourly_grid/year=2004/era5_hourly_2004-01.parquet",
        columns=["latitude", "longitude"],
    ).drop_duplicates()
    sample["longitude_wgs84"] = sample.longitude.where(sample.longitude <= 180, sample.longitude - 360)
    cells = gpd.GeoDataFrame(
        sample,
        geometry=[box(lon - 0.125, lat - 0.125, lon + 0.125, lat + 0.125)
                  for lat, lon in zip(sample.latitude, sample.longitude_wgs84)],
        crs="EPSG:4326",
    )
    equal_area = "EPSG:5070"
    cells_ea = cells.to_crs(equal_area)
    zones_ea = zones.to_crs(equal_area)
    intersections = gpd.overlay(
        cells_ea,
        zones_ea[["weather_zone", "geometry"]],
        how="intersection",
        keep_geom_type=False,
    )
    intersections["intersection_area_m2"] = intersections.geometry.area
    cell_area = cells_ea.set_index(["latitude", "longitude"]).geometry.area
    intersections["grid_cell_area_m2"] = [cell_area.loc[(lat, lon)] for lat, lon in zip(intersections.latitude, intersections.longitude)]
    intersections["grid_cell_fraction"] = intersections.intersection_area_m2 / intersections.grid_cell_area_m2
    zone_area = intersections.groupby("weather_zone").intersection_area_m2.transform("sum")
    intersections["zone_weight"] = intersections.intersection_area_m2 / zone_area
    matrix = intersections[[
        "weather_zone", "latitude", "longitude", "longitude_wgs84",
        "intersection_area_m2", "grid_cell_area_m2", "grid_cell_fraction", "zone_weight",
    ]].copy()
    system = matrix.copy()
    system["weather_zone"] = "SYSTEM"
    system = system.groupby(["weather_zone", "latitude", "longitude", "longitude_wgs84"], as_index=False).agg(
        intersection_area_m2=("intersection_area_m2", "sum"),
        grid_cell_area_m2=("grid_cell_area_m2", "first"),
        grid_cell_fraction=("grid_cell_fraction", "sum"),
    )
    system["zone_weight"] = system.intersection_area_m2 / system.intersection_area_m2.sum()
    matrix = pd.concat([matrix, system], ignore_index=True)
    matrix.to_parquet(OUT / "ercot_weather_zone_grid_weights.parquet", index=False)

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "validated_approximate",
        "county_count": len(counties),
        "unassigned_texas_counties": int(254 - len(counties)),
        "zone_count": len(zones),
        "grid_cells_with_intersection": int(matrix[matrix.weather_zone != "SYSTEM"][["latitude", "longitude"]].drop_duplicates().shape[0]),
        "weight_rows": len(matrix),
        "zone_weight_sums": matrix.groupby("weather_zone").zone_weight.sum().to_dict(),
        "crosswalk_sha256": sha256(crosswalk_path),
        "census_sha256": sha256(census_path),
        "crs_source": "EPSG:4326",
        "crs_area": equal_area,
        "corrections": {**corrections, "zone:SOUTH": "SOUTHERN"},
    }
    (OUT / "mapping_validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    REPORT.write_text(
        "# ERCOT weather-zone mapping provenance\n\n"
        "Status: **approximate secondary mapping; not authoritative ERCOT geometry**.\n\n"
        "ERCOT confirms that Attachment C of its PUCT Project 52691 historical weather study "
        "contains a county-to-zone Excel crosswalk, but the attachment was not retrievable from "
        "the public index during this phase. The implemented fallback uses the published 2026 "
        "Energy Economics replication crosswalk (Zenodo DOI 10.5281/zenodo.23653132) joined to "
        "official 2024 Census county cartographic boundaries. The source crosswalk contains 210 "
        "ERCOT-footprint counties and eight load/weather zones. `Houtson` was corrected to `Houston`.\n\n"
        "Grid cells are 0.25° polygons. Intersections and area weights are calculated in CONUS "
        "Albers EPSG:5070, preserving partial and coastal intersections. Counties absent from the "
        "crosswalk remain unassigned and are not treated as ERCOT service area.\n\n"
        f"Validation summary: `{json.dumps(summary, sort_keys=True)}`\n"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
