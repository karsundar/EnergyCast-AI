# Project status

Project: EnergyCast AI

Last updated: 2026-11-14

## Current state

- Git repository initialized on branch `main`.
- Phase 1 is active.
- Phase 1C bulk acquisition and normalization are complete; all 23 ERCOT annual load files for 2004–2026 are cached and checksummed.
- The 18-entry manifest passes extended schema validation; the selected EIA workbooks and annual ERCOT monthly peak/energy forecast were reused without duplicate downloads.
- Modular source handlers support direct FRED and New York Fed plans, ERCOT/EIA/GPR catalog discovery, and disabled CDS request planning.
- Discovery verified 23 ERCOT historical artifacts (2004–2026), 15 ERCOT forecast workbooks, 12 EIA workbooks, and one official monthly GPR workbook.
- Validated downloads use atomic writes, cache reuse, bounded 429/503 retries, content/signature/parseability checks, and post-validation SHA-256 ledgers.
- Eighteen focused downloader and normalization tests pass.
- Phase 1B acquired and inventoried the eight approved workbooks plus all 12 economic series; no climate data was requested.
- Seven canonical Parquet outputs are available under `data/processed/`; the machine inventory and Phase 1C quality reports contain hashes, coverage, counts, missingness, schemas, reconciliation, and storage sizes.
- No training work has started.
- Existing ROCm and ComfyUI environments have not been modified.

## Blocking decisions

- CDS credentials and `cdsapi` are not installed/configured in the project environment.
- ERA5 and seasonal request parameters remain deliberately unvalidated and disabled; the seasonal system identifier must be selected from the live CDS catalog.
- ERCOT 2016 contains one missing eight-zone interval on 2016-11-06; it is retained as incomplete rather than fabricated.
- The 2021 source `ERCOT` total differs from the simultaneous regional sum by as much as 2,359.83 MW; canonical system aggregates use simultaneous regional sums as required.
- The 11 available FRED CSVs were manually downloaded and validated; future revision history still requires ALFRED integration.
- The FRED GSCPI endpoint was unavailable, so the official New York Fed producer workbook was acquired and validated instead (346 monthly rows, 1998-01 through 2026-10).

## Next action

1. Resolve or formally accept the documented ERCOT 2016 missing interval and 2021 source-total discrepancy.
2. Configure and validate CDS access before changing either Copernicus entry to enabled.
3. Complete the Phase 1 validation gate after the disabled climate datasets are explicitly resolved.
4. Do not proceed to training until every configured dataset passes that gate.

