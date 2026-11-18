# Phase 3A bounded ERA5 alternative-source validation

Generated: 2026-11-15

## Decision

Use the anonymous AWS Earthmover ERA5 mirror for historical backfill through its validated coverage, with the repository snapshot recorded per partition. It is the fastest tested provider that is numerically equivalent to CDS within float32/GRIB sub-area packing precision. Retain CDS for dates newer than the AWS snapshot and for authoritative reconciliation. Do not use Google ARCO as a silent substitute for the canonical series because it is regridded and was not numerically equivalent at the selected precision bounds.

No bulk acquisition was initiated and no additional CDS request was submitted.

## Provider findings

| Provider | Access | Cost/licence | Runtime coverage | Six variables | Result |
|---|---|---|---|---|---|
| AWS Earthmover Icechunk ERA5 | Anonymous S3 | AWS Open Data sponsored transfer; CC BY 4.0 | 1940-01-01–2026-06-30 | Yes | Equivalent; recommended |
| Copernicus ARCO Zarr | Local CDS API key; accepted licence | Free; CC BY 4.0 | 1940-01-01–2026-11-08 | Yes | Equivalent but slow for sparse sample |
| Google Cloud ARCO ERA5 | Anonymous HTTPS API | Public dataset access free; Copernicus licence | 1940-01-01–2026-06-30 | Yes | Fast, but regridded values differ |

The validated variables are 2 m temperature, 2 m dewpoint, 10 m U/V wind, total precipitation and surface solar radiation downwards. All providers returned float32 with units K, K, m s-1, m s-1, m and J m-2 respectively.

## Exact 2024 sample comparison

The sample contains 2024-01-01 at 00, 06, 12 and 18 UTC over the 44×54 Texas grid. Every provider returned the exact grid and timestamps with zero missing observations.

AWS and official Copernicus ARCO matched CDS within the selected packing bounds. For AWS, maximum absolute differences were 0.000244 K for temperature, 0 for both wind components, 0.001192 mm for precipitation and 0 J m-2 for solar radiation. Copernicus ARCO produced the same comparison result, with precipitation also bit-identical in this sample.

Google maximum absolute differences were 0.001007 K, 0.000381 m s-1, 0.000417 mm and 34.25 J m-2. These differences are small for weather-feature use but confirm that the analysis-ready regridding is not a value-identical mirror.

Accumulated `tp` and `ssrd` values were compared directly as one-hour accumulations ending at `valid_time`; they were not differenced or summed during equivalence testing.

## Measured performance

Host network-byte figures are interface deltas and can include unrelated traffic; artifact bytes and durations are exact process outputs.

| Provider/request | Remote read | Total | Host receive delta | Peak RSS | Output |
|---|---:|---:|---:|---:|---:|
| Google, four timestamps | 4.13 s | 6.45 s | 63,024,148 B | 578,076,672 B | 246,720 B |
| AWS, four timestamps | 114.31 s | 116.72 s | 335,032,772 B | 578,076,672 B | 367,142 B |
| Copernicus ARCO, four timestamps | 132.27 s | 142.12 s | 53,141,983 B | 578,076,672 B | 283,758 B |
| AWS, complete January 2004 | 139.00 s | 141.67 s | 744,589,976 B | 578,076,672 B | 42,570,928 B |

AWS uses chunks of 8,736×12×12 for these variables. Texas selection therefore reads bounded spatial tiles rather than the global grid, but a monthly request incurs an annual time-chunk read. This layout should be reused across all months in a year rather than reopening the same annual chunks month by month. Google uses 1×721×1440 chunks and therefore reads global spatial fields for every requested hour. Copernicus time-chunked ARCO uses 1×721×1024 chunks and also reads much more than the Texas subset.

## January 2004 CDS validation

The manually downloaded CDS archive is 16,570,247 bytes with SHA-256 `cd50e4fec24b924f254c06f3b362885e422ffc45bc1ab79db25237778d08d8a3`. It contains 744 hourly timestamps, 44×54 cells and all six variables with no missing values. The full AWS month has identical coordinates and timestamps. Winds and solar radiation are bit-identical; maximum differences are 0.000244 K for temperature and 0.002384 mm for precipitation, both within the recorded packing bounds.

## Implementation

The provider interface canonicalizes variable names, time coordinate and longitude convention while retaining provider identity, URL/bucket and source version or snapshot. Existing atomic output, SHA-256, cache sidecar and normalization mechanisms remain applicable. Focused provider/acquisition tests pass.

Before bulk acquisition, update the orchestration to cache AWS annual spatial tiles or year partitions so the same 8,736-hour chunks are not transferred twelve times. Keep CDS for July–October 2026 unless a newer equivalent AWS snapshot is verified.
