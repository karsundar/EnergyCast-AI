# Decisions

## 2026-11-14 — Acquisition safety boundary

- Catalog pages are discovery inputs, never dataset artifacts.
- A file is moved from its temporary path only after HTTP, Content-Type, signature, expected-format, and minimum-parseability checks pass.
- Artifact URLs and filenames come only from authoritative catalog responses; none are synthesized.
- Duplicate URLs and destination filenames are removed during discovery.
- ERCOT historical discovery is restricted to the configured 2004–2026 window; forecast discovery is independent.
- Current FRED series are marked as candidates for revision tracking. Future vintage-aware collection will use ALFRED rather than treating current observations as immutable.
- CDS requests are monthly chunks over the configured U.S. bounds. Credentials remain in local CDS configuration and are never read into logs.
- Both Copernicus entries remain disabled while `request_parameters.validated` is false.

## 2026-11-14 — Phase 1B bounded samples

- Each acquired artifact was capped at 64 MiB and finalized only after validation.
- EIA coverage uses `HS861M 1990-2009.xlsx` plus the current `sales_revenue.xlsx`, verified locally as 2010 through 2026-08.
- The representative forecast is `2025-ERCOT-Monthly-Peak-Demand-and-Energy-Forecast.xlsx`; its primary adjusted forecast has 240 monthly rows covering 2025–2044.
- ERCOT hour-ending values remain in Central prevailing time. DST duplicates and gaps must be normalized during canonical ingestion, not silently discarded during acquisition.
- Eleven FRED series use manually acquired official FRED CSVs. GSCPI uses the official New York Fed producer workbook—not a mirror—because the FRED endpoint was under maintenance.
- The New York Fed download is named `.xlsx` upstream but has an OLE/XLS signature; it is stored as `gscpi_data.xls` so validation follows the actual file format.

## 2026-11-14 — Phase 1C normalization

- Only the 23 ERCOT annual load artifacts, the two selected consolidated EIA workbooks, and the annual ERCOT monthly peak/energy forecast family are in acquisition scope. Mid-term ERCOT metrics and unrelated EIA annual workbooks are excluded.
- ERCOT hour-ending labels are converted to observed interval starts. Fall-back duplicates are disambiguated in source-row order; spring-forward gaps are mapped from the observed hour-ending interval without synthesizing a record.
- Historical weather-zone aliases are mapped to eight canonical zones. Source filenames and quality flags remain on every canonical hourly record.
- System demand is the simultaneous sum of the eight regional observations. Daily and monthly peaks are maxima of that simultaneous sum, never sums of separate regional peaks.
- EIA retail sales remain a separate dataset from ERCOT grid demand. EIA revenue is converted from thousand dollars to USD; prices remain cents/kWh.
- Economic and geopolitical observations are neither interpolated nor forward-filled. Missing publication dates and vintage history remain null/unavailable.
- The missing ERCOT 2016 interval and 2021 source-total discrepancy are reported as source anomalies and are not fabricated or silently reconciled.
