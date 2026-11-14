# Preliminary data-quality report

Generated: 2026-11-14T19:15:34.292244+00:00

## Result

- Acquired and inventoried 20 bounded artifacts.
- Blocked: 0 FRED artifacts because agent-triggered requests to the official host returned no bytes before timeout.
- Copernicus remained disabled; no climate data was requested.

## Observations

- ERCOT 2004 uses `Hour_End` and legacy zone labels (`FAR_WEST`, `NORTH_C`, `SOUTHERN`, `SOUTH_C`); 2020+ uses `Hour Ending`/`HourEnding` and (`FWEST`, `NCENT`, `SOUTH`, `SCENT`). Values are MW.
- ERCOT timestamps are hour-ending Central prevailing time. DST normalization remains required before merging years.
- EIA 1990–2009 includes an `OTHER` sector absent from the current workbook; the current consolidated workbook covers 2010 through its reported current month.
- EIA units are embedded in the three-row header: thousand dollars, MWh, customer count, and cents/kWh. Current-period rows are preliminary while older rows are final.
- GPR is monthly and contains global and country index families plus variable-label metadata. Missing early values reflect series availability and were not imputed.
- The selected ERCOT forecast is the explicitly named monthly peak-demand and energy workbook published 2025-04-08. Its unit labels are not embedded and remain a documentation warning.
- Manually imported FRED CSVs are validated for header, date coverage, missing values, and duplicate dates; units and seasonal-adjustment metadata require the FRED series metadata page.
- ERCOT URL publication dates identify initial catalog publication. For rolling annual files, they can precede the final observation and are not equivalent to the last update time.

## Artifact validation summary

| Artifact | Rows | Start | End | Duplicate keys | Status |
|---|---:|---|---|---:|---|
| HS861M 1990-2009.xlsx | 12240 | 1990-01-01 | 2009-12-01 | 0 | warning |
| sales_revenue.xlsx | 10200 | 2010-01-01 | 2026-08-01 | 0 | warning |
| 2004_ercot_hourly_load_data.xls | 8784 | 2004-01-01 | 2004-12-31 | 1 | warning |
| Native_Load_2020.zip | 8784 | 2020-01-01 | 2020-12-31 | 1 | warning |
| Native_Load_2025.zip | 8760 | 2025-01-01 | 2025-12-31 | 1 | warning |
| Native_Load_2026.zip | 7295 | 2026-01-01 | 2026-10-31 | 0 | warning |
| 2025-ERCOT-Monthly-Peak-Demand-and-Energy-Forecast.xlsx | 240 | 2025-01-01 | 2044-12-01 | 0 | warning |
| fredgraph.csv | 958 | 1947-01-01 | 2026-10-01 | 0 | warning |
| fredgraph.csv | 490 | 1986-01-01 | 2026-10-01 | 0 | warning |
| fredgraph.csv | 502 | 1985-01-01 | 2026-10-01 | 0 | warning |
| fredgraph.csv | 868 | 1954-07-01 | 2026-10-01 | 0 | warning |
| gscpi_data.xls | 346 | 1998-01-31 | 2026-10-31 | 0 | warning |
| fredgraph.csv | 1293 | 1919-01-01 | 2026-09-01 | 0 | warning |
| fredgraph.csv | 358 | 1997-01-01 | 2026-10-01 | 0 | warning |
| fredgraph.csv | 319 | 1947-01-01 | 2026-07-01 | 0 | warning |
| fredgraph.csv | 441 | 1990-01-01 | 2026-09-01 | 0 | warning |
| fredgraph.csv | 126 | 1900-01-01 | 2025-01-01 | 0 | warning |
| fredgraph.csv | 609 | 1976-01-01 | 2026-09-01 | 0 | warning |
| fredgraph.csv | 946 | 1948-01-01 | 2026-10-01 | 0 | warning |
| data_gpr_export.xls | 1522 | 1900-01-01 | 2026-10-01 | 0 | pass |

## ERCOT schema fingerprints

```json
{
  "2004_ercot_hourly_load_data.xls": [
    "hour_ending",
    "COAST",
    "EAST",
    "FAR_WEST",
    "NORTH",
    "NORTH_C",
    "SOUTHERN",
    "SOUTH_C",
    "WEST",
    "ERCOT"
  ],
  "Native_Load_2020.zip": [
    "hour_ending",
    "COAST",
    "EAST",
    "FWEST",
    "NORTH",
    "NCENT",
    "SOUTH",
    "SCENT",
    "WEST",
    "ERCOT"
  ],
  "Native_Load_2025.zip": [
    "hour_ending",
    "COAST",
    "EAST",
    "FWEST",
    "NORTH",
    "NCENT",
    "SOUTH",
    "SCENT",
    "WEST",
    "ERCOT"
  ],
  "Native_Load_2026.zip": [
    "hour_ending",
    "COAST",
    "EAST",
    "FWEST",
    "NORTH",
    "NCENT",
    "SOUTH",
    "SCENT",
    "WEST",
    "ERCOT"
  ]
}
```

## CDS configuration (do not execute yet)

1. Create an account at the Copernicus Climate Data Store and sign in.
2. Open each configured ERA5 and seasonal dataset page and accept its current license/terms.
3. Copy the personal API URL and key into `~/.cdsapirc`; keep that file outside this repository and restrict its permissions.
4. Install only into the isolated project environment: `.venv/bin/python -m pip install cdsapi`.
5. Confirm `cdsapi.Client()` authenticates without printing credentials.
6. Re-open the live dataset forms and validate every variable, area, format, product type, originating centre, seasonal system, initialization month, lead time, and ensemble-member parameter.
7. Set `request_parameters.validated` and `enabled` to true only after a one-month dry-run request plan is reviewed. Keep monthly chunks and the configured U.S. bounds.
8. Validate that seasonal outputs preserve forecast reference time, lead month, and ensemble member before expanding coverage.
