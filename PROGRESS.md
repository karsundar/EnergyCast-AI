# Progress

## 2026-11-17 — Phase 5B provider completion

- Completed and independently validated CDS NCEP CFSv2 system 2: 227/227
  partitions, 11,483,472 normalized grid/member rows, 169,094,473 raw bytes,
  701,687,339 processed bytes, and zero pending or invalid partitions.
- Retained two recovered failure events in the acquisition audit; both affected
  initialization months were subsequently acquired and validated.
- Generated 671,112 NCEP regional member rows and 12,258 ensemble rows with
  zero duplicate keys and no synthetic hindcast availability timestamps.
- Completed the exact-common SEAS5/NCEP comparison across 12,258 regional rows
  from 2004-01 through 2026-11. Provider differences are descriptive; the
  equal-provider mean remains explicitly uncalibrated.

## 2026-11-16 — SEAS5 regional processing

- Processed all 227 validated ECMWF SEAS5 system 51 partitions without network
  access or redownloads.
- Generated 406,134 member-level regional rows and 12,258 ensemble-summary
  rows across eight approximate ERCOT zones plus system, with zero duplicate
  forecast keys and no missing seasonal variables.
- Preserved 25-member retrospective hindcasts with null `available_at` and
  51-member archived forecasts with provider-scheduled availability metadata.
- Generated 7,668 operational rows for leads 1–12. Leads 1–6 use SEAS5;
  leads 7–12 use explicitly labeled expanding ERA5 normals fitted only from
  history preceding each forecast origin.
- Added focused validation for member counts, availability, target alignment,
  duplicate keys, fallback labeling and unsupported features. All eight Phase
  5B focused assertions pass; pytest was unavailable, so they were invoked
  directly without changing dependencies.
- The operator-started CDS NCEP bulk acquisition continues independently.
- Prepared checksum-gated NCEP regional processing and exact-common provider
  comparison. Production execution refuses incomplete acquisition by default.
  A temporary bounded test on the first three ledger-validated months passed
  without reading or changing active request state; no official NCEP regional
  output was emitted.
- Diagnosed the overnight NCEP runner stalled after 168/227 partitions because
  the CDS library defaulted to 500 internal retries following HTTP 500. Bounded
  library retries to one so the runner's configured four-attempt exponential
  backoff regains control on resume; cached validated partitions are reusable.
- The resumed NCEP run reached 185/227 before an incomplete object-store response
  raised `ChunkedEncodingError`. Added this interrupted-body condition to the
  typed transient retry policy; schema and normalization failures remain terminal.

## 2026-11-15 — Phase 4C closure and Phase 5A preparation

- Preserved final leaders: E14 oracle energy 3.247%, E14 oracle peak 5.403%,
  E14 non-oracle energy 4.409%, and E0 non-oracle peak 6.305% WAPE.
- E15/E16 are complete. E13 full fine-tuning remains deferred after completed
  validation; E17 full rolling evaluation remains pending after smoke.
- E16 meets nominal system coverage but remains under-covered regionally; final
  test residuals were not used for additional calibration.
- Added dry-run-first SEAS5 forecast/hindcast planning, NetCDF validation and
  leakage-safe climate normals. No seasonal request was submitted.
- The operator manually acquired the bounded January 2024 SEAS5 forecast as a
  79,398,432-byte GRIB1 file. Its 612 messages exactly match six variables ×
  two leads × 51 members; all message boundaries and terminators are valid.
  An isolated ecCodes reader confirmed the six variables, 2024-01-01 issue,
  leads ending at 744/1440 hours and member IDs 0-50. It also found the manual
  file is a global 360×180 grid, so this route is not approved for bulk use.
- Matching API samples passed: January 2016 hindcast (25 members) and January
  2024 forecast (51 members) share a Texas 11×14 grid, six variables, units and
  two leads, with zero missing/nonfinite values. No bulk request was submitted.
- Normalized 7,700 hindcast and 15,708 forecast grid/member rows and generated
  11,016 leakage-safe climate-normal rows. Fixed NOAA discovery to traverse
  bounded THREDDS subcatalogs; the initial zero-artifact result reflected
  hierarchical catalog structure, not an empty archive.
- The bounded 5.3 MB NOAA CFSv2 GRIB2 passed checksum and 101-message structural
  validation. Remote subsetting is unavailable; only 168/72,960 grid points
  intersect Texas, and the flux file lacks 10 m V wind. NOAA direct-file bulk
  acquisition is rejected; SEAS5 remains primary.
- CDS NCEP CFSv2 system 2 bounded forecast/hindcast validation passed with
  124/28 members, the full six-variable schema, Texas subsetting and zero
  missing values. Phase 5A bounded provider validation is complete; no bulk
  seasonal acquisition is authorized.

## 2026-11-15 — Phase 4C implementation and smoke handoff

- Implemented E11-E17 runners without launching long jobs.
- Built 38,880 development-only OOS validation predictions from existing caches.
- Added target/geography/horizon calibration with relative and scale-normalized
  residuals, minimum sample fallback, interval width, coverage and pinball loss.
- CPU smoke tests passed for E14-E17. Chronos GPU smoke remains pending because
  `.venv-chronos2` is not installed.
- The isolated Chronos environment was subsequently installed manually. E11
  bounded inference passed on ROCm in FP32 and BF16. BF16 batch 64 is the
  provisional choice: 272,952,320 peak allocated bytes and 0.0127 seconds after
  warm-up, versus 512,094,208 bytes for FP32.
- E11-E13 bounded energy and peak smoke gates passed. E13 produced finite losses,
  finite ordered quantiles, and reloadable 4.85 MB LoRA adapters for both targets.
- Completed all 2018-2020 Chronos validation partitions: 216 files, 23,328 rows,
  36 origins, zero duplicates/nonfinite/crossing. System validation WAPE was
  E11 2.811% energy/4.701% peak, E12 oracle 2.208%/3.643%, and E13 2.771%/4.684%.
- Completed E11/E12 test inference and exact-common evaluation. E14
  validation-weighted oracle-mixed ensemble established new system bests:
  3.247% energy WAPE and 5.403% peak WAPE. Non-oracle E14 improved energy to
  4.409% but did not beat the 6.305% E0 peak reference.

## 2026-11-15 — Phase 4B full-run handoff

- Prepared resumable E8-E10 rolling-origin execution with configurable workers
  and LightGBM thread limits.
- Added atomic checkpoints, progress ledger, graceful SIGTERM handling, runtime
  and worker peak-RAM recording.
- Added forecast-origin leakage validation, development-only level-versus-growth
  selection, and validation-only empirical P10-P90 intervals.
- Added exact-common-observation E0-E10 evaluation for WAPE, MAE, RMSE, bias and
  interval coverage. Full execution remains manual and has not been started.

Last updated: 2026-11-14

## Completed

- Extended all 18 active manifest entries with acquisition strategy, source handler, allowed formats, file pattern, authentication, request parameters, and enabled state.
- Added source-aware discovery for FRED, ERCOT, EIA, Copernicus, and the Geopolitical Risk catalog.
- Added content-based CSV, XLS, XLSX, ZIP, NetCDF, and GRIB validation with atomic finalization and post-validation SHA-256 recording.
- Added bounded retry behavior for HTTP 429 and 503 responses.
- Added discovery, dry-run, dataset selection, and manifest-validation modes.
- Verified official catalog discovery without downloading artifacts: ERCOT load (23 annual artifacts for 2004–2026), ERCOT forecast (15 workbooks), EIA-861M (12 workbooks), and GPR (one monthly workbook).
- Generated discovery ledgers under `reports/`.
- Passed 14 focused tests; no editor diagnostics reported.
- Phase 1B acquired eight bounded artifacts: ERCOT load for 2004/2020/2025/2026, EIA consolidated 1990–2009 and 2010–current workbooks, the monthly GPR workbook, and the representative 2025 ERCOT monthly peak/energy forecast.
- Imported and validated all 12 configured economic series. Eleven use official FRED CSVs; GSCPI uses the official New York Fed workbook because the FRED endpoint was unavailable.
- Phase 1B inventory now contains 20 acquired artifacts and no blocked artifacts.
- Generated `reports/data_inventory.json` and `reports/data_quality_preliminary.md` with hashes, schemas, coverage, missingness, duplicate keys, units, geography, publication metadata, and validation status.
- Phase 1C acquired the remaining 19 ERCOT annual load artifacts and reused four validated ERCOT samples, both selected EIA workbooks, and the selected annual ERCOT forecast workbook.
- Normalized 1,601,272 ERCOT regional hourly records for 2004-01 through 2026-10, with UTC/local timestamps, interval energy, source lineage, canonical weather zones, and DST quality flags.
- Generated daily and monthly ERCOT system aggregates from simultaneous regional sums, plus canonical EIA, economic, geopolitical, and official forecast Parquet datasets.
- Added four focused normalization/aggregation tests; the combined focused suite now contains 18 tests.
- Generated `reports/data_quality_phase1c.json` and `reports/data_quality_phase1c.md`; updated `reports/data_inventory.json` with Phase 1C acquisition and processed-output metadata.
- Built monthly energy, peak, month-over-month growth, and year-over-year growth targets for all eight ERCOT weather zones and the simultaneous regional system sum.
- Added leakage-safe known-future calendar features, origin-available demand lags, trailing averages, growth history, and peak history. Economic and geopolitical feature stores remain separate.
- Implemented seasonal naive, drift seasonal naive, Holt-Winters, and direct multi-horizon LightGBM baselines with rolling monthly origins and horizons 1–12.
- Selected LightGBM parameters using 2018–2019 only, calibrated empirical P10/P50/P90 intervals using 2020 residuals only, and evaluated test targets from 2021 onward.
- Generated 61,344 forecast rows, including 55,728 evaluated rows and 5,616 explicitly incomplete future-horizon rows, plus 4,440 metric breakdown rows.
- Added MAE, RMSE, MAPE, WAPE, MASE, bias, directional accuracy, year-over-year growth error, peak error, interval coverage, and quantile loss reporting.
- Added three forecast-accuracy charts and five focused Phase 2A tests.
- Recreated the proven ROCm PyTorch stack in the isolated project environment and installed Lightning 2.6.6 plus PyTorch Forecasting 1.8.0 without modifying either reference environment.
- Passed the three-epoch AMD-GPU TFT smoke test for energy and peak targets, including finite gradients/predictions and checkpoint reload.
- Trained and evaluated the validation-selected small TFT on the AMD Radeon AI PRO R9700. System WAPE was 18.238% for energy and 14.712% for peak, so it did not beat Holt-Winters.
- Saved small-TFT checkpoints, 12-horizon backtest predictions, probabilistic metrics, GPU memory measurements, and forecast-horizon charts.
- Passed all 25 focused tests after TFT training, including both Phase 2B decoder-leakage tests.
- Completed an identical-observation audit across TFT and all four Phase 2A baselines: 12,744 common target/geography/origin/horizon keys, with zero actual-value mismatch and correct timestamp/horizon alignment.
- Confirmed checkpoint inverse scaling, original-unit P50 selection, convergence and early stopping behavior; no defect requiring retraining was found.
- Generated encoder, decoder, static-variable, and temporal-attention interpretations plus actual/predicted, residual, regional-error, and horizon-error charts. Interpretations are descriptive, not causal.
- Added comparable MAE, RMSE, MAPE, WAPE, MASE, direction, YoY, bias, seasonal, COVID-overlap, extreme-load-proxy, regional, system, and horizon results.
- Added regional/horizon P10-P90 coverage, interval width, quantile loss, and crossing reports. TFT coverage is 15.66% for energy and 28.19% for peak overall, with zero crossing.
- Proved future-decoder placeholder invariance using values 0 and 1,000,000,000 (maximum prediction delta 0.0), then generated 12-month Holt-Winters, LightGBM, and experimental TFT forecasts.
- Finalized the Phase 2B dependency lock, GPU report, diagnostic report, calibration report, evaluation report, charts, and machine-readable artifact inventory.

## Remaining Phase 1 work

- Add ALFRED vintage-aware collection before treating revised FRED history as immutable.
- Configure `cdsapi`, validate CDS request vocabulary, then explicitly enable climate requests.
- Resolve or accept the documented ERCOT 2016 missing interval and 2021 source-total reconciliation anomaly.
- Complete the Phase 1 gate after climate-source validation.
- Do not begin training.

## Remaining Phase 2 work

- Improve empirical interval calibration; current post-2021 system coverage is below the nominal 80% level for every baseline.
- Phase 2B is complete. Do not run medium/large TFT experiments because the small configuration underperformed materially; do not begin Qwen integration, climate acquisition, or dashboard work.

## Phase 3A setup

## Phase 5B seasonal bulk preparation

- Completed and audited the primary SEAS5 priority run: 227/227 requests,
  6,949,404 rows, 547,778,684 combined raw/processed bytes, zero failures and
  zero invalid partitions. Coverage includes 2004-01–2016-12 retrospective
  hindcasts and 2021-01–2026-11 archived forecasts. CDS NCEP remains pending.
- Validated the anonymous NOAA/AWS CFSv2 operational fallback against the CDS
  NCEP sample. Indexed byte ranges retrieve all required native fields using
  479,775 bytes instead of 5,239,300 bytes; temperature, wind, precipitation
  and solar fields agree within documented regridding/packing tolerances.
  Full fallback bulk was not started because it would require ~52,000 objects,
  ~25 GB, and still would not supply matching 2004–2016 hindcasts.
- Confirmed ECMWF SEAS5 system 51 coverage: retrospective hindcasts 1981–2016,
  archived forecasts from 2017 and operational system 51 from November 2022.
- Confirmed CDS NCEP CFSv2 system 2 coverage: hindcasts 1993–2016 and archived
  forecasts from October 2019. Hindcasts retain null availability timestamps.
- Prepared a 227-request priority plan per provider for project-overlap
  hindcasts (2004–2016) and test-period forecasts (2021–2026-11), preserving
  individual members, six leads, initialization/target months and provenance.
- Added sequential resumable acquisition, cache/checksum validation, atomic
  files, transient retry/backoff, disk monitoring, graceful shutdown, progress
  ledgers and a no-network completion validator. No bulk request was submitted.
- Built and validated approximate equal-area seasonal-grid weights for eight
  ERCOT zones plus system. Added exact operator commands and measured-sample
  storage/runtime estimates in `docs/PHASE5B_EXECUTION.md`.

- Verified live official catalogue identifiers, coverage, variables, formats, product types, seasonal systems, lead times, and licences for the two scoped Copernicus datasets.
- Restricted both requests to the Texas/ERCOT bounding box and added the complete requested ERA5/seasonal variable set.
- Installed `cdsapi==0.7.7` in the isolated project environment under the ROCm constraints.
- Added explicit bounded CDS sample and bulk modes with temporary-file validation, checksums, cache reuse, and cleanup.
- Estimated the uncompressed 2004–2026-10 Texas ERA5 hourly payload at approximately 11.42 GB; reserve 15–25 GB working space pending a measured sample.
- Configured CDS authentication outside source control, confirmed ERA5 licence acceptance, and completed a bounded Texas request for 2024-01-01 at 00/06/12/18 UTC.
- Validated the 182,505-byte sample (SHA-256 `286381bde6fe90ddd6f02905038e89f01cd8c845216fc7d5cf14c2e293769fd2`). CDS packaged the response as a ZIP containing separate instantaneous and accumulated NetCDF products; the acquisition validator now handles this documented multi-file response safely.
- Inspected both bounded-sample NetCDF members with xarray and preserved complete global/variable metadata, checksums, dimensions, coordinates, units, coverage, and missingness in `reports/phase3a_era5_sample_inspection.json`.
- Implemented Kelvin/Celsius/Fahrenheit, relative humidity, vector wind speed, precipitation, SSRD energy/flux, Texas-local/DST daily aggregation, and grid-cell-first 65°F HDD/CDD transformations.
- Generated 9,504 clean hourly sample grid-cell rows and 4,752 explicitly incomplete daily sample grid-cell rows in Parquet; both have zero missing values and duplicate keys.
- Hardened CDS acquisition for single NetCDF and multi-member ZIP responses with safe atomic extraction, archive/member SHA-256, bounded 429/503 retry, request-fingerprint cache identity, corrupt/incomplete rejection, and stale partial recovery.
- Measured sample-based storage at approximately 9.13 GB raw archives, 9.12 GB extracted NetCDF, 19.36 GB conservatively scaled hourly Parquet, 0.81 GB daily Parquet, and 0.03 GB monthly Parquet. Reserve 45–50 GB if all raw/extracted/processed layers are preserved.
- All 22 focused acquisition/weather tests pass. `pip check` previously reported no broken requirements; Torch `2.13.0+rocm10.1.0`, HIP `7.16.26385`, and GPU visibility remain unchanged.
- Seasonal licence/sample validation and authoritative machine-readable ERCOT weather-zone boundaries remain unresolved.
- Added inclusive CDS month bounds plus resumable monthly acquisition/normalization orchestration. The focused downloader suite passes 18 tests.
- Verified 3,299,547,402,240 bytes (about 3.1 TB) free, exceeding the 50 GB gate.
- Submitted only January 2004 for complete-month validation. CDS accepted it but reported dataset-wide saturation (100 running, 1,906 queued), so no artifact was downloaded or normalized.
- Stopped the local waiter intentionally and submitted no later months. Resume state is recorded in `reports/era5_history_progress.json`; January must be checked for an existing completed server-side result before any retry.
- The January 2004 CDS request subsequently completed and its manually downloaded 16,570,247-byte archive was safely copied, extracted, checksummed and validated (744 hours, 44×54 cells, six variables, zero missing values).
- Validated AWS Earthmover, official Copernicus ARCO and Google Cloud ARCO against the same four-timestamp 2024 CDS sample. AWS and Copernicus are equivalent within float32/GRIB packing precision; Google is regridded and differs slightly.
- Benchmarked the complete January 2004 AWS Texas subset: 139.00 s remote read, 141.67 s total, 42,570,928-byte NetCDF and 578,076,672-byte peak RSS. Host receive delta was 744,589,976 bytes and may include unrelated host traffic.
- Added a reusable canonical ERA5 provider interface with provider/version provenance and passed 24 focused ERA5/acquisition tests. No bulk acquisition or additional CDS request was initiated.
- Added `scripts/run_era5_aws_annual.py` for user-controlled annual, resumable AWS acquisition with atomic NetCDF caches, request fingerprints, SHA-256 validation, retries/backoff, persistent JSON progress, timestamped logs, disk guards, graceful SIGTERM/SIGINT handling, monthly Parquet normalization, and validated-partition skipping. No historical bulk run was initiated by the agent.
- Passed the bounded local synthetic-January test and focused ERA5 suites (9 tests). The runner deliberately defers an intermediate December until the next annual cache supplies the Texas-local accumulated-variable boundary.
- The independent annual AWS run completed for the currently complete range. A deep local audit validated 23 annual caches and 269 monthly partitions from 2004-01 through 2026-05: 196,494 cached source hours including the final local-boundary hours, 196,488 normalized UTC hours, 466,855,488 hourly rows, 19,452,312 daily rows and 639,144 monthly rows.
- All annual and monthly checksums, source variables, null counts, timestamps, grid coordinates, cross-year boundaries, Parquet schemas and actual hourly/daily/monthly keys passed. Duplicate keys, missing source timestamps and source null values are zero. The 44×54 grid is unchanged across years.
- Total weather storage is 20,193,169,677 bytes. The only configured physical flag is 2,376 SSRD values of `-0.0014299653 J/m²` at 2004-12-04 05:00 UTC (one per cell); the source values are retained and documented.
- Added the consolidated machine-readable/Markdown audit and updated the main inventory. All 27 relevant acquisition/weather tests pass; no dataset was redownloaded and no model training started.
- Official ERCOT discovery still yields an image map rather than machine-readable weather-zone geometry. CDS seasonal access requires authenticated CDS, CC-BY acceptance and the additional non-European-contribution licence; forecast/hindcast systems must match. No seasonal request was submitted.

## Phase 3B geographical weather-demand integration

- ERCOT's FAQ identifies an authoritative Attachment C Excel county crosswalk in PUCT Project 52691. Because the public attachment was not retrievable, implemented a clearly labeled secondary approximation using the 2026 Energy Economics/Zenodo eight-zone county crosswalk and official 2024 Census county geometry.
- Built 210-county zone polygons and 2,167 equal-area grid intersection weights in EPSG:5070 for eight canonical zones plus `SYSTEM`. Partial/coastal cells and 44 unassigned Texas counties remain explicit.
- Aggregated the 466.9-million-row grid source with partitioned DuckDB reads into 1,768,392 hourly, 73,683 daily and 2,421 monthly regional rows through 2026-05.
- Joined weather and ERCOT demand into 1,768,338 hourly, 73,683 daily and 2,421 monthly observations. Corrected the secondary mapping's `SOUTH` label to canonical `SOUTHERN`; the 2016 missing demand interval remains nine null zone/system values.
- Added descriptive weather-demand statistics and a temperature-energy chart. Associations are not interpreted causally.
- Added a disabled bounded seasonal forecast/hindcast configuration and exact licence steps. No seasonal request or model training started.
- All 30 relevant Phase 3B, acquisition and weather tests pass.

## Phase 4A weather-aware oracle forecasting

- E6 energy, E6 peak, and E7 energy full TFT commands were reported complete by the operator. E7 peak status remains separate.
- Added configurable E6/E7 loader, batch-size, FP32/BF16, and determinism controls while preserving the original defaults.
- Added a bounded E7 one-variable-at-a-time profiler for data loading, host transfer, forward, backward, optimizer, throughput, RAM, VRAM, GPU utilization, and projected epoch duration. It has not been launched while the operator's training may still be active.
- The completed E7 profiler identifies batch 512 as the fastest validated single change: 75.74 samples/s, 513.8 seconds projected per epoch and 10.55 GB peak reserved VRAM, versus 50.87 samples/s and 770.2 seconds for the reference. Loader workers did not materially help; backward computation dominates. BF16 alone reached 65.62 samples/s. Strict determinism remains unsupported by the ROCm upsample backward operation.
- Corrected E7 energy reporting to distinguish the selected best checkpoint loss (6234.81 at epoch 2) from the final early-stopping epoch loss (8240.61 at epoch 12); the checkpoint itself was valid and unchanged.
- E6/E7 checkpoint-only inference and identical-observation evaluation are complete: 91,584 comparable E0-E7 predictions. System WAPE is E6 16.917% energy / 11.772% peak and E7 11.620% energy / 11.446% peak. Neither TFT wins: Holt-Winters is best for energy (4.765%), while oracle SARIMAX is best for peak (5.730%, versus Holt-Winters 6.305%).
- E7 peak was stopped by the operator after 29 completed epochs; epoch 23 remains validation-selected at loss 361.515. The interrupted epoch produced no checkpoint and is excluded.

- Added a single configuration for chronological splits, completeness, CPU models and small TFT settings.
- Implemented E0 Holt-Winters, E1 weather-oracle SARIMAX, E2/E3 monthly LightGBM without/with oracle weather, and E4/E5 recursive daily LightGBM without/with oracle weather.
- Daily target lags after forecast origin use prior predictions. Daily energy is summed to monthly energy; daily system peak is maximized, never summed.
- Daily training eligibility independently checks weather completeness and 23/24/25-hour Texas-local demand completeness, preserving the documented 2016 missing interval without imputation.
- Implemented E6/E7 monthly/daily small TFT datasets with observed weather as known-future variables and demand strictly unknown in the decoder. CPU fallback is forbidden.
- CPU smoke validation passed across E0-E5 on 12 common predictions. One-epoch ROCm smoke validation passed for E6 monthly energy and E7 daily peak with finite losses.
- The first full eight-worker E0-E5 run completed 636 partitions in 163.9 seconds and produced 68,688 common prediction rows. Smoke outputs remain isolated.
- Added configurable spawned-process execution for E0-E5 (default eight workers), two LightGBM threads per worker, one BLAS/OpenMP thread, shared immutable feature caches, atomic per-task checkpoints, resume support, and per-experiment timing/worker-RAM reports.
- The original `fork` benchmark path deadlocked after native thread initialization and was replaced with `spawn`. The apparent four-worker consistency failure was confirmed as task-completion-dependent DataFrame column ordering, not differing predictions; benchmark comparisons now canonicalize rows and columns before exact equality and hashing.
- Post-run reconciliation found some E4/E5 final horizons omitted a leap day or month-day 31 because timestamp `DateOffset` did not preserve the future month end. Calendar-period month-end construction and a leap-year regression test now pass; E4/E5 require regeneration before final metrics.
- Regenerated all E4/E5 partitions with complete calendar-month boundaries. Final evaluation succeeded with 68,688 comparable predictions and canonical monthly actual reconciliation.
- Final ERCOT-system WAPE: E0 4.765% energy / 6.305% peak; E1 4.911% / 5.730%; E2 11.212% / 10.495%; E3 10.926% / 10.060%; E4 13.661% / 18.031%; E5 11.061% / 16.785%. Weather-aware results remain explicitly oracle-only.
# Phase 5C Stage 1 preparation

- Added the Stage 1 weather-skill evaluator, configuration, focused leakage tests and execution handoff.
- Bounded smoke validation passed on 5,832 exact ERA5/provider matches across both providers and leads 1–6; no fabricated operational availability or 2017–2020 archived forecasts were introduced.
- Full weather-skill evaluation remains a manual command. No electricity model training or Stage 2 experiment was started.
- Full Stage 1 and a versioned corrected-wind rerun completed on 23,598 rows. Initial and corrected outputs are both preserved.
- Review confirms useful temperature skill only; interval undercoverage and wind/humidity definition mismatches block Stage 2 pending feature-scope approval.

## Phase 5C Stage 1.5

- Generated 23 versioned seasonal-compatible ERA5 partitions (2,421 regional monthly rows) without modifying canonical ERA5 or Phase 5B data.
- Comparable full weather skill and temperature lead/season/region/anomaly diagnostics are complete.
- Operational feature smoke and the full 61,344-row build passed with all audit violations and duplicate business keys at zero. No model training started.

## Phase 5C Stage 2 Gate A

- Implemented E18 with a direct multi-horizon LightGBM schema that can be reused unchanged for E19/E20. Forecast demand history is frozen at origin and weather inputs are expanding climate normals only.
- The bounded smoke run passed: four E18 forecasts and 16 identical-key E0/E11/E14/E18 comparison rows, with zero oracle rows. Smoke metrics are diagnostic only.
- Full Gate A completed with 11,448 E18 predictions and 45,792 exact-key E0/E11/E14/E18 comparison rows. System E18 WAPE is 11.830% energy and 11.443% peak, with substantial negative bias; it does not beat E14 (4.409%/6.542%) or E0 peak (6.305%).
- Gate A found no leakage or alignment defect. E19-E23 remain unimplemented and unlaunched pending the separate Gate B authorization.

## Phase 5C Stage 2 Gate B

- Implemented E19 raw SEAS5 and E20 raw NCEP using one fixed fitted E18-compatible model per target/origin, with expanding-normal fallback for leads 7-12. Provider calibration is excluded and remains reserved for E22.
- The bounded smoke run passed: eight predictions, exact target-specific E14/E0 comparisons, zero oracle rows, zero unrelated-feature differences and exact E18 model reproduction.
- Full Gate B completed with 22,896 E19/E20 predictions and 45,792 common comparison rows. System E19 WAPE is 11.581% energy / 11.303% peak, a small improvement over E18 (11.830% / 11.443%); E20 worsens to 11.905% / 12.033%. E14 energy (4.409%) and E0 peak (6.305%) remain decisively better.
- The full audit retained zero unrelated-feature and fallback-weather differences and exact E18 reproduction. Negative bias remains dominant. Provider releases occur on scheduled days 6/10 inside the lead-1 target month. E21-E23 remain unstarted.

## Phase 5D structural-bias investigation

- Completed a read-only diagnostic of 34,344 E18-E20 predictions; no model was modified or trained. Bias breakdowns cover origin/target year, horizon, season, region, demand level and growth regime, with deterministic paired bootstrap intervals.
- Confirmed level-target extrapolation as the primary mechanism: system actuals exceed historical training maxima in 12.9% of peak and 9.4% of energy rows, predictions do so in 0%, and every above-range row underforecasts. Error severity strongly increases with the actual/training-maximum ratio.
- Month-start seasonal forecasts require normals; both providers are available at the defined mid-month refresh. E25 residual correction is recommended first, then E24 growth targets. E21-E27 remain unstarted.

## Phase 5D E25 bounded validation

- Implemented E25-A/B/C using strictly rolling-origin OOS Holt-Winters residuals, one shared residual model per target/origin, operational weather availability gates and normal fallback for leads 7-12.
- Development-only shrinkage selected lambda 0.50 for energy and 0.25 for peak; lambda zero remains the unchanged Holt-Winters control. The 12-row smoke and 20-row exact E0/E9 comparison passed leakage and issuance checks.
- Full cache extension and evaluation remain manual long-running commands. E24, E26 and E27 were not started.
- Full E25 completed with 34,344 predictions and 57,240 identical-observation rows. E25-A wins energy at 4.703% system WAPE; E25-B wins peak at 6.236%. Both improve E0 and E9, but E14 remains best for energy at 4.409%.
- Residual correction reduces energy/peak bias to -0.829 million MWh and -2,183 MW for E25-A. Seasonal provider inputs add no material value over normals. E24/E26/E27 remain unstarted.

## Phase 5D E24 bounded validation

- Implemented E24-A year-over-year log growth, E24-B OOS Holt-Winters log ratio and E24-C shrunken seasonal-naive log ratio with one LightGBM schema and expanding climate normals.
- The smoke produced 12 E24 predictions and 36 exact common-comparison rows. Positivity, reference availability, label cutoff and oracle-weather violations are zero; focused E24/E25 tests pass.
- Development-only lambda selection includes lambda=0 controls. E24-A is fixed at lambda=1; E24-B/C select shrinkage without final-test data. Full rolling evaluation remains manual. E26/E27 remain unstarted.
- Full E24 completed with 34,344 predictions and 103,032 identical-observation rows. E24-B sets a new peak benchmark at 6.201% WAPE and reaches 4.688% energy WAPE; E14 remains the energy benchmark at 4.409%.
- E24-B's peak improvement over E25-B is only 0.035 percentage points and is not conclusive by horizon-band bootstrap intervals. E24-A/C worsen WAPE, and above-range bias remains negative. E26/E27 remain unstarted.

## EnergyCast V1 final-validation bounded gate

- Froze E14, E24-B and E0 candidates. Development-only selection on 2018-2019 chooses E14 energy (2.969% WAPE) and E24-B peak (4.320%); 2020 is held separately for interval calibration and 2021+ remains untouched during fitting.
- Implemented target/geography/horizon scale-normalized P10/P90 calibration with same-geography fallback, separate system/regional reporting, interval width, pinball loss, coverage, training-range behavior and paired model uncertainty.
- Bounded validation passes: 24 calibrated forecasts, zero oracle rows, zero quantile crossings and a valid 12-horizon contract. Month-start/day-15 and provider/fallback audits are explicit. Full evaluation remains manual; deployment and E26/E27 have not started.
- Full V1 evaluation completed with 11,448 forecasts. Deterministic benchmarks are confirmed at 4.409% energy WAPE (E14) and 6.201% peak WAPE (E24-B), with paired improvements over E0 excluding zero.
- Calibration-only selection chose relative symmetric conformal scaling, but final P10-P90 coverage is only 53.5%/57.7% system energy/peak and 69.1%/65.4% regionally. Do not tune widths on final-test outcomes; probabilistic deployment remains blocked for review.

## V1 pre-test-safe calibration remedy

- Implemented scale-normalized symmetric conformal, scale-normalized asymmetric quantile and relative asymmetric quantile candidates with fixed horizon bands and same-geography fallback.
- Forecast-origin-blocked evaluation admits residuals only after target-month close and reports coverage, width and pinball loss by horizon band, geography, origin block and growth regime. System and regional methods are selected separately.
- The bounded 2019-H1 gate generated 1,296 candidate rows with zero final-test reads and cutoff violations; 10 V1 calibration tests pass. Full four-fold development evaluation remains manual.
- No untouched historical holdout remains. Deterministic forecasts stay enabled; probabilistic intervals remain disabled pending prospective or independent confirmation. E26/E27 remain unstarted.
- Full four-fold remedy evaluation completed with 11,340 development predictions and zero final-test/cutoff violations. Selected coverage is 80.5% energy-system, 74.2% energy-regional, 91.9% peak-system and 80.8% peak-regional.
- Material origin-block, horizon and high-growth conditional miscalibration remains. Frozen tables are retained only as prospective research candidates; interval serving remains disabled.
- Implemented deterministic EnergyCast V1 production scaffolding with frozen
  E14/E24-B manifests, strict source/candidate validation, atomic publication,
  immutable history, monitoring, rollback, refresh recovery, shadow-only
  calibration and a non-activating monthly schedule preview.
- Completed the bounded 2025-04 production replay: 540 candidate rows generated
  216 twelve-month forecasts and exactly reproduced saved E14 energy and E24-B
  peak predictions (maximum absolute delta 0.0). Three focused tests pass and a
  simulated failure did not alter the prior forecast artifact.
- Resolved the fitted-artifact gate with an authorized one-time freeze at the
  2026-10 cutoff. The 503,484,976-byte bundle vendors Chronos-2, 27 fitted
  statistical states, E9/E24-B LightGBM models, schemas, fixed weights,
  transformations, inference inputs, dependency versions and checksums.
- Two fresh-process inference runs and the default production dry-run each
  generated 216 finite deterministic forecasts; fresh-run maximum delta is 0.0.
  Four focused tests pass, corrupt bundles are rejected, and public probabilistic
  fields remain absent. No live forecast, scheduler activation or Git push occurred.
