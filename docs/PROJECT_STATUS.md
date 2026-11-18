# Project status

## Deterministic V1 production preparation

- Deterministic production is frozen to E14 non-oracle energy (4.409% WAPE)
  and E24-B peak (6.201% WAPE). Public probabilistic intervals remain disabled.
- The atomic production runner, immutable history, monitoring join, rollback,
  refresh rollback, structured logs, idempotent forecast IDs, scheduler preview
  and deterministic report are implemented.
- A 2025-04 replay produced 216 forecasts and reproduced both frozen experiment
  outputs exactly (maximum absolute difference 0.0). Three focused tests pass;
  a simulated failure preserved the existing output checksum.
- Option B resolved the fitted-artifact blocker. Bundle
  `energycast-v1.0.0-frozen-202610` contains 27 fitted statistical states, two
  LightGBM models, E14 weights, schemas, inference inputs and the pinned local
  Chronos-2 checkpoint. Ten artifacts totaling 503,484,976 bytes are checksummed.
- Two fresh-process runs generated 216 finite forecasts each with maximum delta
  0.0. Strict checksum validation and four focused tests pass. Publication and
  scheduling remain inactive by instruction; rollback against an actual current
  production forecast cannot be exercised until a first publication is approved.

## Phase 4C preparation

- E11-E13 Chronos-2 execution supports ROCm-only enforcement, FP32/BF16,
  configurable batches, bounded benchmarking, atomic partitions, validation
  partitions, and resume. The isolated environment is installed; E11, E12 and
  two-step E13 smoke tests passed for both energy and peak using BF16 batch 64.
- E14-E17 are implemented. CPU smoke tests passed for validation-weighted
  ensembling, bias correction, scale-aware calibration, and non-weather SARIMA
  residual correction.
- Scale-normalized calibration outperformed relative calibration on development
  pinball loss, but bounded 2020 coverage was only about 56-58%, below the 80%
  target. Full runs remain manual.
- Chronos validation acquisition is complete: 216 partitions/23,328 rows with
  no duplicates, nonfinite values, or quantile crossings. E12 oracle led
  system validation (2.208% energy WAPE, 3.643% peak); E11 non-oracle achieved
  2.811% and 4.701%. Two-step E13 did not materially improve E11, so full E13
  fine-tuning is deferred pending a justified hypothesis.
- E11/E12 identical-test evaluation completed with 148,824 common predictions.
  E11 non-oracle scored 4.457% energy and 6.783% peak system WAPE. E12 oracle
  scored 3.650% and 5.513%. Validation-weighted E14 improved the oracle-mixed
  result to a new best 3.247% energy and 5.403% peak; non-oracle E14 scored
  4.409% energy and 6.542% peak.

## Phase 4B preparation

- E8-E10 rolling-origin CPU backtesting is implemented with 8 workers by
  default, bounded native threads, atomic partition checkpoints, progress after
  every task, graceful stop, and resume.
- Historical residual targets are checked for forecast-origin leakage. Absolute
  versus percentage correction and P10-P90 calibration use development data
  only. E10 remains explicitly oracle; E8/E9 are non-oracle.
- Sequential and multiprocessing smoke validation passed. The full rolling run
  has intentionally not been launched; commands are in `docs/PHASE4B_EXECUTION.md`.

Project: EnergyCast AI

Last updated: 2026-11-17

Phase 4C is closed. E13 validation, E15 and E16 are complete; E17 has passed
smoke testing but its full rolling backtest remains pending. E16 P10-P90
coverage is 80.8%/85.5% system energy/peak and 68.7%/75.9% regionally. Phase 5A
bounded seasonal acquisition is prepared with SEAS5 system 51 as primary and
NOAA CFSv2 as a secondary candidate. Matching January 2016 hindcast and January
2024 forecast samples pass on the same Texas 11×14 grid with 25/51 members,
six variables, two leads and zero missing values. CDS NCEP CFSv2 system 2 also
passes with 28/124 hindcast/forecast members. Phase 5A bounded validation is
complete; no bulk job started.

## Current state

- Phase 5B primary ECMWF SEAS5 acquisition is complete and independently
  validated: 227/227 initialization months, 6,949,404 normalized rows,
  110,580,266 raw bytes, 437,198,418 processed bytes, zero pending/invalid
  partitions and zero failures. Secondary CDS NCEP is also complete: 227/227
  requests, 11,483,472 normalized rows, 169,094,473 raw bytes, 701,687,339
  processed bytes, and zero pending/invalid partitions.
- Completed primary SEAS5 regional processing produced 406,134 member-level,
  12,258 ensemble-summary, and 7,668 operational 12-month rows. Validation
  confirms zero duplicate keys, 25-member hindcasts, 51-member archived
  forecasts, and no synthetic hindcast availability timestamps.
- Operational leads 1–6 use archived SEAS5 forecasts. Leads 7–12 use explicitly
  labeled, forecast-origin-safe expanding ERA5 normals. Unsupported dewpoint
  and probabilistic fallback fields remain null; no daily extremes are inferred.
- NCEP regional processing and exact-common SEAS5 comparison are prepared with
  a strict 227-partition production gate. A bounded frozen-ledger smoke test on
  three completed hindcasts passed: 66,528 source rows, 3,888 regional member
  rows, 162 ensemble rows, zero duplicate keys, and actual per-initialization
  member counts of 28, 24 and 20. Full processing subsequently passed on all
  227 partitions, producing 671,112 member rows and 12,258 ensemble rows.
- The complete exact-common SEAS5/NCEP comparison contains 12,258 rows covering
  2004-01 through 2026-11. Its equal-provider mean is an uncalibrated feature,
  not a validated operational forecast ensemble.
- Bounded NOAA/AWS operational CFSv2 validation passed using indexed GRIB byte
  ranges with 90.843% transfer reduction. The matched CDS-member temperature
  MAE is 0.106 K after regridding. Full reconstruction would require roughly
  52,000 objects/~25 GB and lacks matching historical hindcasts, so it is kept
  as a fallback rather than launched as unapproved bulk traffic.
- Phase 5B bulk acquisition tooling is operational. The sequential,
  resumable runner supports primary ECMWF SEAS5 system 51 and secondary CDS
  NCEP CFSv2 system 2, with request fingerprints, checksums, atomic writes,
  cache recovery, disk guards and persistent per-month progress.
- The priority plan contains 227 requests per provider (2004–2016 hindcasts and
  2021–2026-11 forecasts). SEAS5 is estimated at 6,949,404 normalized rows;
  NCEP at 12,170,928. NOAA direct CFSv2 is excluded and leads 7–12 use labeled
  forecast-origin-safe climate normals.
- A reusable 1° seasonal grid weight matrix validates 154 source cells, 79
  ERCOT-intersecting cells and unit weights for all eight zones plus system.
  It inherits the documented approximate/non-authoritative zone geometry.
- Exact manual commands, coverage, estimates and provider limitations are in
  `docs/PHASE5B_EXECUTION.md`. No model training was initiated.

- Git repository initialized on branch `main`.
- Phase 2A and Phase 2B are complete. Phase 2B includes the ROCm GPU gates, small-TFT training, identical-observation diagnostics, probabilistic evaluation, interpretation, and leakage-safe next-12-month forecasts.
- Phase 3A setup is in progress. CDS authentication and the ERA5 licence are validated, and a bounded Texas ERA5 sample was acquired and validated; seasonal acquisition remains disabled pending its separate licence/sample gate.
- January 2004 CDS acquisition is complete and validated: 744 hourly timestamps, 44×54 Texas cells, six variables and zero missing observations. No later CDS month was submitted.
- Bounded alternative-source validation is complete. AWS Earthmover is anonymous/free and equivalent to CDS within packing precision; Google ARCO is faster for sparse reads but measurably differs because it is regridded; official Copernicus ARCO is equivalent but slower in the tested time-chunked path.
- The annual AWS ERA5 runner is implemented for user-controlled bulk execution. It downloads one year-aware spatial subset at a time, validates request fingerprints and checksums, resumes from persistent progress, normalizes validated monthly partitions, monitors disk space/performance, handles graceful shutdown, and skips validated caches. No bulk run was started by the agent.
- The bounded synthetic January validation and focused ERA5 suites pass (9 tests); they cover monthly hourly/daily/monthly normalization, Texas-local boundaries, cache reuse, request-fingerprint rejection, and the disk guard.
- The user-controlled annual AWS run completed and the deep post-acquisition audit validated 23 annual caches and 269 monthly partitions covering 2004-01 through 2026-05. The outputs contain 466,855,488 hourly, 19,452,312 daily and 639,144 monthly grid-cell rows with zero duplicate keys, missing source timestamps, null source values or spatial-grid changes.
- Weather storage totals 20,193,169,677 bytes: 4,442,584,511 annual raw/cache bytes, 14,859,150,936 hourly Parquet bytes, 860,909,730 daily bytes, 30,105,329 monthly bytes and 419,171 validation-metadata bytes.
- One retained source anomaly is documented: all 2,376 grid cells have SSRD `-0.0014299653 J/m²` at 2004-12-04 05:00 UTC. This minute negative packing-level value is flagged rather than silently changed; all other configured physical-range flags are zero.
- The relevant downloader/ERA5 suites pass 27 tests, and editor diagnostics report no errors in the annual runner, audit script or annual-runner tests.
- Phase 3B produced an explicitly approximate eight-zone mapping from a published 2026 Energy Economics county crosswalk and official Census county boundaries. ERCOT confirms an authoritative Attachment C Excel crosswalk exists in PUCT Project 52691, but that attachment was not retrievable; the fallback is not labeled authoritative.
- Equal-area EPSG:5070 intersections map 1,002 ERA5 cells through 2,167 zone/system weights; all nine geography weight sums equal one. The mapping covers 210 counties and leaves 44 Texas counties unassigned/outside the approximate ERCOT service area.
- Regional weather contains 1,768,392 hourly, 73,683 daily and 2,421 monthly rows through 2026-05. Weather-demand integration contains 1,768,338 hourly, 73,683 daily and 2,421 monthly rows; the documented 2016 missing demand interval remains null rather than imputed.
- Thirty relevant Phase 3B/acquisition/weather tests pass. No model training or seasonal download was initiated.
- Phase 4A weather-aware oracle forecasting is implemented for E0-E7. E0-E5 use a resumable CPU runner; E6/E7 use a GPU-only small TFT runner that refuses CPU fallback. Observed future weather is explicitly tagged as oracle input, and no future demand is exposed to decoder features.
- Bounded validation passed for all paths: E0-E5 produced 12/12 identical-observation system predictions at one origin; E6 and E7 each completed one finite-loss epoch on the AMD R9700. Full Phase 4A experiments were not launched by the agent and no oracle smoke metric is presented as operational accuracy.
- Full manual launch, monitoring, graceful-stop, resume, and evaluation commands are documented in `docs/PHASE4A_EXECUTION.md`. The validated weather overlap limits complete 12-month origins to 2025-04 when coverage ends in 2026-05.
- Phase 4A CPU experiments E0-E5 are complete after correcting and regenerating E4/E5 calendar-month boundaries. The final identical-observation evaluation contains 68,688 predictions across 636 task partitions, with zero pending tasks.
- Phase 4A TFT throughput controls and a bounded E7 profiler are implemented. Original training defaults remain the reference; the benchmark must be run only after active GPU training ends, and no optimized setting has been selected without measurements.
- E7 profiling is complete. Batch 512 FP32 is the fastest directly validated setting (49% higher sample throughput than the batch-16 reference); data loading and transfer are negligible relative to backward computation, so additional loader workers are not recommended. BF16 improved throughput independently but has not yet been validated in combination with batch 512.
- Final Phase 4A E0-E7 evaluation contains 91,584 identical-observation forecasts. Holt-Winters remains best for energy at 4.765% system WAPE; oracle SARIMAX is best for peak at 5.730% versus Holt-Winters 6.305%. E6 monthly TFT oracle records 16.917%/11.772% energy/peak and E7 daily TFT oracle 11.620%/11.446%. TFT outputs were verified in original units with correct timestamp alignment; their negative bias is genuine underforecasting rather than an inverse-scaling defect.
- On the ERCOT system series, oracle weather improves peak WAPE from 6.305% to 5.730% for SARIMAX versus Holt-Winters, from 10.495% to 10.060% for monthly LightGBM, and from 18.031% to 16.785% for daily LightGBM. Energy WAPE improves for LightGBM (11.212% to 10.926% monthly; 13.661% to 11.061% daily) but SARIMAX oracle energy is slightly worse than Holt-Winters (4.911% versus 4.765%). These are oracle findings, not operational forecasts.
- The eight-worker execution completed in 163.9 seconds initially; corrected E4/E5 regeneration completed with eight workers and all final evaluation reconciliation gates passed. E6/E7 full TFT experiments remain pending manual execution.
- Phase 1C bulk acquisition and normalization are complete; all 23 ERCOT annual load files for 2004–2026 are cached and checksummed.
- The 18-entry manifest passes extended schema validation; the selected EIA workbooks and annual ERCOT monthly peak/energy forecast were reused without duplicate downloads.
- Modular source handlers support direct FRED and New York Fed plans, ERCOT/EIA/GPR catalog discovery, and disabled CDS request planning.
- Discovery verified 23 ERCOT historical artifacts (2004–2026), 15 ERCOT forecast workbooks, 12 EIA workbooks, and one official monthly GPR workbook.
- Validated downloads use atomic writes, request-fingerprint cache reuse, bounded 429/503 retries, content/signature/parseability checks, safe NetCDF ZIP extraction, and archive/member SHA-256 ledgers.
- Eighteen focused downloader and normalization tests pass.
- Phase 1B acquired and inventoried the eight approved workbooks plus all 12 economic series; no climate data was requested.
- Seven canonical Parquet outputs are available under `data/processed/`; the machine inventory and Phase 1C quality reports contain hashes, coverage, counts, missingness, schemas, reconciliation, and storage sizes.
- Monthly ERCOT targets cover eight weather zones plus the simultaneous regional system sum from 2004-01 through 2026-10. The 2016-11 incomplete interval remains flagged at 99.861% completeness.
- Leakage-safe feature datasets distinguish known-future calendar variables from origin-available demand history; economic and geopolitical features remain separate and unused by baselines.
- Rolling-origin results cover four models, two targets, horizons 1–12, and test targets from 2021 onward. The prediction artifact contains 55,728 evaluated and 5,616 explicitly incomplete future-horizon rows.
- On ERCOT system test data, Holt-Winters is the strongest current baseline: 4.654% WAPE for monthly energy and 5.867% WAPE for monthly peak demand.
- Twenty-two focused Phase 1/3A acquisition/weather tests pass; the newest suite covers xarray parsing, coordinate/timestamp validation, DST, transformations, archive safety, and cache identity.
- The small TFT was trained locally on the AMD GPU for both targets. Its system test WAPE is 18.238% for energy and 14.712% for peak, materially worse than Holt-Winters (4.654% and 5.867%).
- On the exact 708 common system observations per target, Holt-Winters WAPE is 4.597% for energy and 6.042% for peak versus TFT at 18.238% and 14.712%; the discrepancy is not caused by timestamp, target, unit, P50, inverse-scaling, or WAPE-alignment defects.
- Future TFT predictions are invariant when decoder target placeholders change from zero to one billion (maximum prediction delta 0.0). The next-12-month artifact includes Holt-Winters, LightGBM, and an explicitly experimental TFT forecast.
- TFT P10-P90 coverage is materially under-calibrated (15.66% energy and 28.19% peak across all common regional observations), with zero quantile crossing.
- Existing ROCm and ComfyUI environments have not been modified.
- Host diagnostics can see the AMD devices through `/dev/kfd`, `/dev/dri`, and `rocm-smi`; the discrete `R9700AT` reports 34,208,743,424 bytes of VRAM.
- Two compatible external virtual environments were identified at `/home/karthiks/ai-projects/ai-movie/.venv` and `/home/karthiks/src/CDC_Respiratory_Forecast_Starter/.venv`. Both contain PyTorch `2.13.0+rocm10.1.0` and report HIP `7.16.26385`.
- Read-only verification through the `ai-movie` environment passed: `torch.cuda.is_available()` is true, two devices are visible, device 0 is `AMD Radeon AI PRO R9700`, GPU matrix multiplication produced finite output, and a neural-network forward/backward pass produced finite gradients.
- Neither external environment contains Lightning or PyTorch Forecasting. They remain unmodified and will be used only as compatibility references for an isolated EnergyCast TFT environment.
- The exact verified ROCm stack was recovered from the host pip cache and protected by `constraints-phase2b-rocm.txt`; the Torch wheel SHA-256 is `0732d797ec59b782d658ecf5065e771cd530637c9bf4a0cf0ccf5c82d9174d54`.

## Blocking decisions

- Phase 2B requires an isolated EnergyCast TFT environment with the verified ROCm PyTorch build plus compatible Lightning and PyTorch Forecasting packages. No packages were installed while verifying the external environments.
- Pandas was safely changed from `3.1.1` to `3.0.6` for PyTorch Forecasting compatibility. All 23 pre-existing tests pass, all seven canonical Parquets remain readable, Phase 2A targets/features reproduce exactly, and Holt-Winters WAPE remains 4.654436% for energy and 5.867498% for peak demand.
- The isolated project environment now contains PyTorch `2.13.0+rocm10.1.0`, HIP `7.16.26385`, Lightning `2.6.6`, and PyTorch Forecasting `1.8.0`; `pip check` passes and both reference environments remain untouched.
- The small TFT smoke test passed on `AMD Radeon AI PRO R9700` for energy and peak targets: three epochs each, finite losses/gradients/predictions, successful checkpoint reload, expected `9 x 12 x 3` prediction tensors, and no target-derived decoder features.
- CDS credentials are configured outside source control with mode `600`. The ERA5 bounded request succeeded and produced a 182,505-byte ZIP containing separate instantaneous and accumulated NetCDF products; both members passed signature and archive validation. Seasonal remains disabled pending its separate licence and sample validation.
- The ERA5 sample has 44×54 cells at 0.25°, four timestamps over 2024-01-01, zero missing/duplicate observations, and separate instantaneous (`t2m`, `d2m`, `u10`, `v10`) and one-hour accumulated (`tp`, `ssrd`) products. Hourly/daily sample Parquets preserve grid cells and timezone-aware timestamps.
- An authoritative machine-readable ERCOT eight-weather-zone geometry/county crosswalk has not yet been verified; no polygon boundaries will be inferred from a visual map.
- ERCOT's official site currently provides a weather-zone map as a JPEG and defines the purpose of weather zones, but discovery still found no official machine-readable polygon or county crosswalk. The image will not be digitized and presented as authoritative geometry.
- CDS seasonal monthly/original single-level products require authenticated CDS access, CC-BY acceptance and the additional licence for non-European contributions. Forecast/hindcast data must use matching forecasting systems. Seasonal acquisition remains disabled until these terms are accepted and a bounded member/lead-time sample passes validation.
- ERCOT 2016 contains one missing eight-zone interval on 2016-11-06; it is retained as incomplete rather than fabricated.
- The 2021 source `ERCOT` total differs from the simultaneous regional sum by as much as 2,359.83 MW; canonical system aggregates use simultaneous regional sums as required.
- The 11 available FRED CSVs were manually downloaded and validated; future revision history still requires ALFRED integration.
- The FRED GSCPI endpoint was unavailable, so the official New York Fed producer workbook was acquired and validated instead (346 monthly rows, 1998-01 through 2026-10).

## Next action

- Phase 5C Stage 2 Gate A is complete: 11,448 E18 predictions and 45,792 identical-observation comparison rows. E18 underperforms with system WAPE of 11.830% energy and 11.443% peak and strong negative bias. Gate B remains a controlled seasonal-weather-input comparison; E19-E23 are deferred.
- Gate B E19/E20 is implemented and smoke-validated without launching the full run. Raw provider weather changes only leads 1-6; leads 7-12 exactly retain normals. The full manual command and scheduled-release latency caveat are documented; E21-E23 remain deferred.
- Gate B full evaluation is complete. Raw SEAS5 modestly improves E18 but remains far behind E14/E0; raw NCEP worsens both targets. The systematic negative bias is not corrected. Feature-isolation audits passed and E21-E23 remain deferred for review.
- Phase 5D diagnostics confirm level-target extrapolation under structural growth as the principal E18-E20 limitation. The read-only 34,344-row audit and E24-E27 designs are complete; no experiment was trained. E25 is recommended first, followed by E24, subject to separate approval.
- E25-A/B/C is implemented and bounded validation passes. Development-only lambda is 0.50 energy and 0.25 peak; residual availability, weather issuance and exact E0/E9 comparison gates pass. Full OOS cache extension/training is a manual command and has not been launched. E24/E26/E27 remain unstarted.
- Full E25 is complete. E25-A energy WAPE is 4.703% and E25-B peak WAPE is 6.236%; residual correction reduces systematic bias and outperforms E0/E9, but seasonal weather adds no material increment over normals. E14 remains best for energy. E24/E26/E27 remain unstarted.

1. Use Holt-Winters as the best validated monthly model; treat TFT forecasts as experimental.
2. Do not run medium or large TFT configurations or retrain TFT without a separately approved, testable hypothesis.
3. Weather is validated through 2026-05. When the AWS snapshot contains data through at least 2026-11-01 Texas-local boundary hours, rerun the annual runner with `--end-year 2026 --end-month 10`; validated 2004–2025 caches will be reused.
4. Obtain an authoritative machine-readable ERCOT weather-zone boundary source before grid-to-zone aggregation.
5. Keep seasonal acquisition disabled until its separate licence/sample gate passes; use CDS or a later validated AWS snapshot only for coverage newer than the current AWS snapshot.

# Phase 5C Stage 1 weather-skill validation (2026-11-17)

- Implemented leakage-safe SEAS5/NCEP comparison against regional ERA5 for five common monthly variables and leads 1–6.
- Archived forecasts require recorded availability; retrospective hindcasts remain a separate research track with null availability. The 2017–2020 archive gap is not bridged.
- Added expanding climate normals, chronologically available bias correction, lead-wise deterministic/probabilistic metrics, and validation-only provider weighting.
- A bounded 5,832-row smoke run passed all availability, target alignment, lead and gap checks. Full Stage 1 evaluation is prepared for manual execution; Stage 2 E18–E23 remains gated on review.
- The operator completed the full 23,598-row run. Audit found and version-corrected spatial wind aggregation without altering Phase 5B artifacts; the corrected result is `reports/phase5c/full_v2`.
- Archived-test skill favors calibrated NCEP temperature (1.423 °C MAE versus 1.478 °C for expanding normals). Normals remain better for precipitation, solar, humidity and wind. Wind/humidity comparisons are proxy-only because monthly seasonal inputs cannot reproduce hourly-derived ERA5 definitions.
- Availability timestamps encode documented provider release schedules rather than file-specific publication audits. Treat results as schedule-based operational simulation. Stage 2 remains gated.

## Phase 5C Stage 1.5 operational weather readiness (2026-11-17)

- Built a versioned 2004-01–2026-05 ERA5 reference matching seasonal monthly aggregation: monthly-mean U/V then vector speed, and monthly-mean temperature/dewpoint then Magnus RH, before regional weighting.
- Re-evaluated 23,598 provider/ERA5 matches. Comparable wind and humidity are now valid, but expanding normals retain lower final-test MAE. NCEP's final-test temperature advantage is not used for selection: 2021–2023 development selects SEAS5 for every system lead.
- Implemented chronological provider selection, calibrated standalone provider temperature, optional precipitation/solar ablations, five-variable normals and explicit lead 7–12 normal fallback.
- The bounded smoke and full 61,344-row operational build passed with zero missing/nonfinite values, duplicates, cutoff, availability, target-alignment or unsupported-lead violations. Targets cover 2021-01 through 2027-10; Stage 2 remains unstarted.

## Phase 5D E24 (2026-11-17)

- E24-A/B/C implementation and bounded validation are complete. Smoke validation has zero leakage, positivity and reference-availability violations and exact E0/E9/E14/E25 common observations.
- Full rolling E24 evaluation is prepared for manual resumable execution but has not been launched. E26/E27 remain unstarted.
- Full E24 is complete: 34,344 E24 predictions, 103,032 common-comparison rows and zero leakage/positivity/reference violations. E24-B is the new peak benchmark at 6.201% WAPE; E14 remains best for energy at 4.409%. E26/E27 remain unstarted.

## EnergyCast V1 final validation (2026-11-18)

- Bounded final-selection/calibration validation is complete. Development-only 2018-2019 selection chooses E14 energy and E24-B peak; 2020 is isolated for scale-aware interval calibration and 2021+ is excluded from all fitting.
- Geography/horizon and same-geography fallback calibration, system/regional metrics, issuance/provider audits, paired uncertainty and 12-month output validation are implemented. The smoke has zero oracle rows and quantile crossings; 12 calibration-focused tests pass.
- Full final evaluation is prepared for manual execution. Deployment work and E26/E27 remain unstarted pending review.
- Full V1 evaluation is complete. E14 energy remains 4.409% WAPE and E24-B peak 6.201%. Operational/leakage and 12-month generation gates pass, but P10-P90 coverage is only 53.5%/57.7% system and 69.1%/65.4% regional. Deterministic selection is validated; probabilistic deployment is blocked and final-test residuals must not be used to retune intervals.
- A separate pre-test-safe calibration-remedy framework is implemented. Three predefined methods, four forecast-origin blocks, fixed horizon bands, geography-only fallback and conditional coverage audits are available. The smoke reads no final-test rows and passes 10 focused tests. Full development-only selection remains manual; intervals remain disabled because no untouched historical confirmation set remains.
- Full remedy development evaluation is complete: 11,340 rows, four blocked folds and zero final-test/cutoff violations. Coverage is 80.5%/74.2% system/regional energy and 91.9%/80.8% system/regional peak. Conditional high-growth coverage remains weak, so probabilistic deployment stays disabled.

