# ERCOT weather-zone mapping provenance

Status: **approximate secondary mapping; not authoritative ERCOT geometry**.

ERCOT confirms that Attachment C of its PUCT Project 52691 historical weather study contains a county-to-zone Excel crosswalk, but the attachment was not retrievable from the public index during this phase. The implemented fallback uses the published 2026 Energy Economics replication crosswalk (Zenodo DOI 10.5281/zenodo.23653132) joined to official 2024 Census county cartographic boundaries. The source crosswalk contains 210 ERCOT-footprint counties and eight load/weather zones. `Houtson` was corrected to `Houston`.

Grid cells are 0.25° polygons. Intersections and area weights are calculated in CONUS Albers EPSG:5070, preserving partial and coastal intersections. Counties absent from the crosswalk remain unassigned and are not treated as ERCOT service area.

Validation summary: `{"census_sha256": "da4051717caec55c75e3748c3608c2a3dbde8d1ff401bbaf4f952e3c3fb63ef1", "corrections": {"Houtson": "Houston", "zone:SOUTH": "SOUTHERN"}, "county_count": 210, "created_at": "2026-11-15T14:55:44.178997+00:00", "crosswalk_sha256": "ff8048deca5eae888bb72c6f8ef4486d115fee1aab731f80dcb10541f8985c08", "crs_area": "EPSG:5070", "crs_source": "EPSG:4326", "grid_cells_with_intersection": 1002, "status": "validated_approximate", "unassigned_texas_counties": 44, "weight_rows": 2167, "zone_count": 8, "zone_weight_sums": {"COAST": 1.0, "EAST": 1.0, "FAR_WEST": 1.0, "NORTH": 1.0, "NORTH_CENTRAL": 1.0, "SOUTHERN": 0.9999999999999999, "SOUTH_CENTRAL": 0.9999999999999999, "SYSTEM": 1.0, "WEST": 1.0}}`
