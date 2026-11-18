# ERA5 post-acquisition validation

Generated: 2026-11-15T14:40:44.812355+00:00

Status: **validated** for the requested range through May 2026.

## Coverage and counts

- Annual caches: 23 (2004–2026).
- Normalized months: 269 (2004-01 through 2026-05).
- Source UTC hours: 196,494.
- Hourly grid rows: 466,855,488.
- Daily grid rows: 19,452,312.
- Monthly grid rows: 639,144.
- Duplicate hourly/daily/monthly keys: {'hourly': 0, 'daily': 0, 'monthly': 0}.
- Annual missing timestamps, duplicate timestamps, missing variables and null observations: zero.
- Grid is consistent at 44×54 cells, 0.25°, across all annual caches.
- Every annual boundary is contiguous by one UTC hour.

## Storage

- Annual NetCDF caches and sidecars: 4,442,584,511 bytes.
- Hourly Parquet: 14,859,150,936 bytes.
- Daily Parquet: 860,909,730 bytes.
- Monthly Parquet: 30,105,329 bytes.
- Validation metadata: 419,171 bytes.
- Total: 20,193,169,677 bytes.

## Quality findings

- Negative solar radiation: 2,376 values; minimum -0.001429965253919363 J/m²; timestamps ['2004-12-04 05:00:00+00:00']. Values are retained and explicitly flagged rather than altered.
- All other configured physical-range flags are zero.
- Parquet schemas are stable within hourly, daily and monthly products.

## Remaining 2026 coverage

Normalized weather ends at 2026-05. Required but unavailable months: 2026-06, 2026-07, 2026-08, 2026-09, 2026-10. The validated AWS snapshot ends 2026-06-30; June local-day aggregation needs the July 1 Texas-local boundary, and October requires data through November 1.

## External discovery

- ERCOT publishes an authoritative weather-zone map image and describes the zones, but no authoritative machine-readable polygon or county crosswalk was located. The zone-mapping blocker remains open.
- CDS seasonal monthly and original single-level products require authenticated CDS access, acceptance of CC-BY terms, and the additional licence for non-European contributions. Forecasts must be paired with the matching-system hindcasts. No seasonal request was submitted during this audit.
