# Phase 3B weather-demand integration

Status: complete with an explicitly approximate secondary weather-zone mapping.

## Mapping provenance

ERCOT's weatherization FAQ states that Attachment C of the historical weather study filed in PUCT Project 52691 is an Excel county-to-weather-zone mapping. The public attachment could not be retrieved during this phase. The operational fallback is the published 2026 Energy Economics replication crosswalk from Zenodo DOI `10.5281/zenodo.23653132`, joined to official 2024 U.S. Census county cartographic boundaries.

This fallback is not presented as authoritative ERCOT geometry. It maps 210 counties into the eight canonical load/weather zones. Forty-four Texas counties remain outside the mapping and therefore outside the approximate service-area union. The source typo `Houtson` is corrected to `Houston`; source zone `SOUTH` is translated to canonical `SOUTHERN`.

Grid cells are represented as 0.25-degree polygons. County unions and grid cells are intersected in CONUS Albers `EPSG:5070`. The weight matrix preserves partial cell intersections, coastal intersections, cell-area differences and unassigned land. It contains 2,167 rows across eight zones plus `SYSTEM`; every geography's weights sum to one.

## Regional datasets

- Hourly regional weather: 1,768,392 rows, nine geographies, 2004-01 through 2026-05.
- Daily regional weather: 73,683 rows.
- Monthly regional weather: 2,421 rows.
- Integrated hourly weather-demand: 1,768,338 rows. The six weather hours before the demand series begins are excluded.
- Integrated daily weather-demand: 73,683 rows.
- Integrated monthly weather-demand: 2,421 rows.
- The documented 2016-11-06 missing demand interval remains nine null demand observations after including `SYSTEM`; weather is present and no value is imputed.

Hourly precipitation and solar radiation remain one-hour spatially averaged accumulations. Daily/monthly precipitation and solar energy are temporally summed after spatial aggregation. Temperature, humidity, wind, HDD and CDD use intersection-area weights. Daily extrema use extrema across intersecting cells. Three-day heat/cold indicators use 100°F maximum and 32°F minimum thresholds and are descriptive initial definitions.

## Exploratory findings

The strongest monthly CDD-energy correlations occur in `NORTH_CENTRAL` (0.813), `COAST` (0.772), `SOUTH_CENTRAL` (0.748) and `EAST` (0.721). The system CDD-energy correlation is 0.681. These are statistical associations, not causal effects. Full regional statistics and the temperature-energy chart are stored with the Phase 3B reports.

## Seasonal acquisition gate

No seasonal request was submitted. Before running the bounded sample:

1. Sign in to the Copernicus Climate Data Store.
2. Open both `seasonal-monthly-single-levels` and `seasonal-original-single-levels` dataset pages.
3. On each Download tab, accept the CC-BY licence and the additional licence for non-European contributions.
4. Keep the API key only in `~/.cdsapirc` with mode `600`.
5. Review `config/seasonal_sample.json`; use ECMWF system 51 for both forecast and hindcast, preserving issue date, lead month and ensemble member.
6. Run only a bounded sample and validate dimensions, members, units and forecast/hindcast system identity before enabling bulk acquisition.

The current sample configuration is intentionally disabled.
