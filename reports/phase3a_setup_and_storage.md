# Phase 3A setup and storage gate

Generated: 2026-11-14

## Bulk acquisition status

The disk gate passed with 3,299,547,402,240 bytes available. Only the January 2004 validation month was submitted. CDS accepted the request but could not start it because the dataset had reached global capacity (100 running and 1,906 queued). The local waiter was intentionally stopped, no response artifact was created, and no subsequent month was submitted. Before resuming, inspect the existing CDS request to avoid a duplicate submission.

## Current status

- Official CDS catalogue metadata and live request vocabularies were inspected for `reanalysis-era5-single-levels` and `seasonal-monthly-single-levels`.
- `cdsapi==0.7.7` is installed only in the isolated EnergyCast environment under the existing ROCm constraints.
- The manifest is restricted to the Texas/ERCOT bounding box `[36.5, -106.75, 25.75, -93.5]` and contains all six requested source variables.
- CDS authentication and the ERA5 licence are validated. One bounded Texas sample was submitted and retained; no bulk acquisition was initiated.
- ERA5 is enabled only behind the explicit `--cds-bulk` switch. Seasonal acquisition remains disabled pending its separate licence and sample validation.

## Official catalogue findings

### ERA5

- Dataset: `reanalysis-era5-single-levels`.
- Catalogue coverage observed: 1940-01-01 through 2026-11-09; operational availability can lag and must be checked per request.
- Atmospheric grid: regular latitude/longitude, 0.25° x 0.25°.
- Temporal resolution: hourly.
- Product type: `reanalysis`.
- Supported requested variables: 2 m temperature, 2 m dewpoint, total precipitation, 10 m U/V wind, and surface solar radiation downwards.
- NetCDF is available but marked experimental in the live form; GRIB is the catalogue-native format.

### Seasonal monthly single levels

- Dataset: `seasonal-monthly-single-levels`.
- Catalogue coverage observed: 1981-01-01 through 2026-11-01.
- ECMWF systems in the live constraints include 4, 5, and 51. System 51 is configured for the representative sample.
- Lead months 1–6 and the requested variables are supported.
- Forecasts and retrospective hindcasts must use the same originating centre and system.
- Applicable licences include CC-BY-4.0 and the additional licence for non-European contributions.
- The sample must confirm member coordinates, native grid, issue/reference time, and the distinction between retrospective hindcasts and archived real-time forecasts.

## Bounded ERA5 sample inspection

- Request: 2024-01-01 at 00:00, 06:00, 12:00, and 18:00 UTC over `[36.5, -106.75, 25.75, -93.5]`.
- Archive: 182,505 bytes; SHA-256 `286381bde6fe90ddd6f02905038e89f01cd8c845216fc7d5cf14c2e293769fd2`.
- CDS returned two NetCDF4 members because instantaneous and accumulated fields use different GRIB step types.
- Instantaneous member: 136,633 bytes; `t2m`, `d2m`, `u10`, `v10`, all float32.
- Accumulated member: 45,523 bytes; `tp`, `ssrd`, both float32.
- ZIP compression provided no saving for these already compressed NetCDF files: archive/extracted ratio 1.0019.
- Both members have dimensions `(valid_time=4, latitude=44, longitude=54)`, descending latitude, ascending longitude, and 0.25° spacing.
- Coverage is 2024-01-01 00:00–18:00 UTC at six-hour sample spacing. Coordinates and timestamps are unique; every source variable has zero missing values.
- Bounds are 25.75–36.5°N and 106.75–93.5°W.
- Instantaneous units: temperature/dewpoint K and wind components m/s. Accumulated units: precipitation m and solar radiation J/m².
- Global metadata identifies ECMWF, CF-1.7, stream `oper`, and the applicable `instant` or `accum` step type. Complete global and variable metadata is preserved in `phase3a_era5_sample_inspection.json`.

The normalized sample contains 9,504 hourly grid-cell rows and 4,752 local-day grid-cell rows. Both outputs have zero missing values and duplicate keys. Daily completeness is only 4.17–12.5% because the bounded sample intentionally requested four timestamps; its daily statistics validate mechanics, not production daily coverage.

## Accumulated-variable semantics

Official ECMWF ERA5 documentation states that reanalysis accumulations cover the one-hour processing period ending at each validity time. They are not running totals. Consequently:

- total precipitation is multiplied by 1,000 from metres to millimetres and summed by interval;
- SSRD is preserved in J/m², converted to MJ/m² by dividing by 1,000,000, and converted to the mean flux for that hour by dividing by 3,600 seconds;
- neither variable is differenced;
- an accumulated interval is assigned to the Texas-local date containing its interval, using the validity timestamp minus one nanosecond at midnight boundaries.

Source: [ECMWF ERA5 data documentation](https://confluence.ecmwf.int/spaces/CKB/pages/76414402/ERA5+data+documentation).

## Measured storage estimate

The Texas bounding box contains at most 44 latitude points x 54 longitude points = 2,376 grid cells at 0.25°. For 2004-01-01 through 2026-10-31, six hourly float32 variables require approximately:

`2,376 cells x 24 hours x 8,340 days x 6 variables x 4 bytes = 11.42 GB`

The period contains 200,160 hours, 8,340 days, and 274 monthly requests. Linear estimates from the small four-timestamp sample are intentionally conservative:

| Layer | Estimated size |
|---|---:|
| Raw CDS ZIP archives | 9.13 GB |
| Extracted NetCDF members | 9.12 GB |
| Six source float32 arrays without container overhead | 11.41 GB |
| Hourly normalized Parquet, measured sample scaling | 19.36 GB |
| Daily grid-cell Parquet | 0.81 GB |
| Monthly grid-cell Parquet | 0.03 GB |
| One monthly archive plus extracted and normalized working set | about 0.14 GB |
| Recommended temporary headroom | at least 1 GB |
| Recommended free space while preserving archives and extracted members | 45–50 GB |

Full-month compression should outperform this tiny sample because fixed metadata overhead is amortized. Actual monthly sizes must be tracked before expanding beyond initial bounded batches.

Recommended format strategy:

- preserve original CDS ZIP archives and checksums as immutable source evidence;
- retain extracted NetCDF by month for xarray-compatible multidimensional source access;
- write hourly normalized data as Zstandard-compressed Parquet partitioned by year/month, processing one month at a time;
- write daily/monthly Parquet separately for modeling;
- consider Zarr only if later analysis requires frequent multidimensional random access across many months. Do not duplicate the entire history into both Parquet and Zarr by default.

Monthly processing avoids loading the full history into RAM. A month of six source float32 arrays is roughly 42 MB before xarray/container overhead; derived hourly arrays should be generated and written in bounded partitions, then released before the next month.

## Manual actions required

1. Accept the separate seasonal dataset licences, including the additional licence for non-European contributions.
2. Run only the configured seasonal bounded sample after licence confirmation.
3. Do not start ERA5 historical bulk acquisition without a separate explicit approval.

## Weather-zone mapping gate

The canonical eight ERCOT zone names are present in the electricity data, but no authoritative machine-readable polygon geometry has yet been verified. Area-weighted grid-cell mapping will not be generated from a visual map or guessed county boundaries. Phase 3A needs an official ERCOT polygon/counties source or a documented authoritative crosswalk before zonal weather aggregation.

## Blocked deliverables

Historical bulk acquisition, seasonal sample/downloads, zone mapping, full weather features, and demand-weather exploratory charts remain blocked. No observations, coverage, or geometries have been fabricated.
