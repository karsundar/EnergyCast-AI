# Phase 1C data-quality report

Generated: 2026-11-14T19:27:15.406868+00:00

## Processed outputs

| Dataset | Rows | Coverage | Size (bytes) | Status |
|---|---:|---|---:|---|
| ercot_hourly | 1601272 | 2004-01-01T06:00:00+00:00 to 2026-11-01T04:00:00+00:00 | 28521029 | valid |
| ercot_daily | 8340 | 2004-01-01T00:00:00+00:00 to 2026-10-31T00:00:00+00:00 | 376015 | valid |
| ercot_monthly | 274 | 2004-01-01T00:00:00+00:00 to 2026-10-01T00:00:00+00:00 | 16978 | valid |
| eia_state_monthly | 124440 | 1990-01-01T00:00:00+00:00 to 2026-08-01T00:00:00+00:00 | 1841340 | valid |
| economic_indicators | 7256 | 1900-01-01T00:00:00+00:00 to 2026-10-31T00:00:00+00:00 | 53688 | valid |
| geopolitical_indicators | 170464 | 1900-01-01T00:00:00+00:00 to 2026-10-01T00:00:00+00:00 | 629933 | valid |
| ercot_official_forecasts | 240 | 2025-01-01T00:00:00+00:00 to 2044-12-01T00:00:00+00:00 | 10287 | valid |

## Validation notes

- ERCOT source files: 23; duplicate canonical UTC/zone keys: 0; missing regional demand values: 8.
- ERCOT coverage: 2004-01-01T06:00:00+00:00 through 2026-11-01T04:00:00+00:00; incomplete local periods: 1.
- DST fall-back hours are disambiguated by source-row order; spring transitions retain only observed intervals. No interval is fabricated.
- Daily and monthly ERCOT peaks are calculated from simultaneous regional sums. Energy equals demand multiplied by observed interval duration.
- ERCOT regional aliases are normalized while each source schema remains recorded in the JSON report.
- Source-total reconciliation exceeds 1 MW only for: Native_Load_2021.zip; detailed maximum and mean differences are in the JSON report.
- EIA revenue is converted from thousand dollars to USD; average price remains cents/kWh. Retail sales remain separate from ERCOT grid demand.
- Economic and geopolitical observations are not interpolated or forward-filled. Source files do not provide publication dates or historical vintages, so those fields remain unavailable.
- Copernicus remains disabled and no climate data was downloaded.
