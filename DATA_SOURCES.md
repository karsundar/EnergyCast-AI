# Data sources

Last discovery verification: 2026-11-14

| Source | Authoritative catalog or endpoint | Strategy | Discovery result | Blocker |
|---|---|---|---|---|
| ERCOT historical load | `https://www.ercot.com/gridinfo/load/load_hist` | Catalog discovery | All 23 annual files for 2004–2026 acquired; 1,601,272 regional hourly records normalized | One missing eight-zone 2016 interval; 2021 source-total reconciliation anomaly |
| ERCOT forecasts | `https://www.ercot.com/gridinfo/load/forecast` | Catalog discovery | Annual monthly peak/energy family selected; 2025–2044 adjusted forecast normalized | Units inferred from official workbook labels and documented |
| EIA-861M | `https://www.eia.gov/electricity/data/eia861m/` | Catalog discovery | Consolidated 1990–2009 and 2010–2026-08 workbooks acquired | Historical `OTHER` sector differs from current schema |
| FRED | `https://fred.stlouisfed.org/graph/fredgraph.csv` | Direct CSV | 11 configured FRED series acquired and validated | ALFRED vintages remain future work |
| New York Fed GSCPI | `https://www.newyorkfed.org/research/policy/gscpi` | Official producer workbook | 346 monthly rows acquired; 1998-01 through 2026-10 verified | Upstream `.xlsx` is an OLE/XLS workbook; stored with the truthful `.xls` suffix |
| ERA5 | `reanalysis-era5-single-levels` through CDS API | Monthly CDS chunks | Request plan only; disabled | CDS authentication, `cdsapi`, and request vocabulary validation |
| Copernicus seasonal | `seasonal-monthly-single-levels` through CDS API | Initialization-month CDS chunks | Request plan only; disabled | System identifier, CDS authentication, `cdsapi`, and request validation |
| Geopolitical Risk | `https://www.matteoiacoviello.com/gpr.htm` | Catalog discovery | Monthly workbook acquired; 1900-01 through 2026-10 verified | Early-series missingness is structural and retained |

Discovery and acquisition ledgers preserve exact resolved URLs, checksums, and publication dates where available. Phase 1C reused cached artifacts and downloaded only missing in-scope ERCOT annual load files; no climate data was requested.
