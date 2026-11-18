# Decisions

## Phase 5C Stage 1

- Score only the five variables common to seasonal products and canonical ERA5 monthly regional data. Use relative humidity for humidity skill; do not fabricate an ERA5 dewpoint target.
- Treat archived forecasts with recorded `available_at` as the only operational backtest source. Keep retrospective hindcasts and their calibration in an explicitly separate research track.
- Use expanding same-calendar-month ERA5 normals with observations strictly before initialization and a ten-year minimum.
- Permit bias correction only from errors whose verifying ERA5 month was available before the current forecast. Use month end plus seven days as the documented ERA5 availability assumption.
- Learn provider weights from the archived 2021–2023 validation period only. Keep the 2012–2014 hindcast research-validation split separate; never use final test periods for selection.
- Do not begin E18–E23 until the complete Stage 1 results are reviewed.
- Preserve the initial Stage 1 report and Phase 5B products. Store the spatially corrected wind ensembles as versioned Phase 5C inputs and the rerun under `full_v2`.
- Label archived evaluation as schedule-based operational simulation because `available_at` records documented release schedules, not file-specific publication audits.
- Do not use the preliminary wind or relative-humidity comparison for provider selection until historical ERA5 features are transformed to seasonal-compatible monthly definitions. The preliminary final-test NCEP temperature result is descriptive and is superseded for selection by the Stage 1.5 development-only decision below.

## Phase 5C Stage 1.5

- Define comparable ERA5 wind as magnitude of monthly-mean U/V at each grid cell before area weighting. Define comparable humidity from monthly-mean temperature/dewpoint with the same Magnus transform before area weighting.
- Do not select NCEP from its 2024–2026 test advantage. Archived 2021–2023 development selects SEAS5 for all six system temperature leads; operational selection must be recomputed chronologically by region/lead and fall back to normals until both providers have 12 observable errors.
- Use expanding normals as defaults for wind and humidity. Permit precipitation and solar seasonal forecasts only as controlled ablations because their development improvements did not persist in final testing.
- Keep release availability labeled schedule-based. Do not represent it as a file-specific archived publication timestamp.

## Phase 4C closure and Phase 5A source gate

- Fit E16 before final test and use the 2020 holdout only for conformal
  inflation. Do not tune further from final-test regional undercoverage.
- Retain E15 as an energy improvement, not a universal correction: peak
  accuracy worsens and negative bias remains.
- Select ECMWF SEAS5 system 51 as primary because hindcast and real-time systems
  match. Preserve issue/availability, system, member and lead metadata.
- Keep NOAA CFSv2 secondary until NCEI spatial subsetting proves global objects
  need not be transferred.
- NCEI returned HTTP 500 for both NCSS and OPeNDAP on the selected operational
  monthly flux artifact. Permit one hard-capped 5.3 MB global validation file,
  but do not approve CFSv2 bulk acquisition from this path.
- Keep ECMWF SEAS5 system 51 as the primary seasonal provider and CDS NCEP
  CFSv2 system 2 as the secondary comparison. Never assign historical
  availability timestamps to retrospective hindcasts.
- Use origin-safe calendar-month normals for unsupported leads and label them
  as a baseline, never as a dynamical forecast.

## Phase 4C

- Never pool raw absolute residuals across regions. Fit calibration by target,
  geography and horizon when sample counts permit; compare relative and
  per-series-scale normalization on development data only.
- Keep E12 and oracle-mixed E14 separate from operational non-oracle rankings.
- Require ROCm GPU visibility for Chronos-2 and prohibit silent CPU fallback.
- Benchmark FP32/BF16 and batch size with bounded inference before E13 full
  fine-tuning; full Chronos and E17 runs are manual.
- Disable TF32 explicitly for Chronos training on ROCm. Use explicit BF16 or
  FP32 because Chronos 2.3.0's NVIDIA capability heuristic misclassifies the
  ROCm CUDA-compatible interface.
- Do not launch full E13 yet: its two-step validation result is effectively tied
  with E11 and does not justify additional fine-tuning cost. Proceed with E11
  non-oracle and E12 oracle test inference first.
- Adopt E14 oracle-mixed as the current research best for both system targets
  (3.247% energy, 5.403% peak). For operational non-oracle use, retain E14 for
  energy (4.409%) and E0 Holt-Winters for peak (6.305%).

## Phase 4B backtesting execution

- Use process-level parallelism with spawn, 8 workers, 2 LightGBM threads per
  worker and 1 native BLAS/OpenMP thread per worker to avoid oversubscription and
  fork-after-OpenMP deadlocks.
- Select absolute versus percentage residual correction only from development
  targets available by 2020-12. Calibrate empirical intervals from that same
  development window; never use final-test residuals.
- Treat E10 as oracle because it consumes observed future weather. E8 and E9 are
  the weather-independent comparisons.

## 2026-11-15 — Phase 4A oracle weather design

### E7 performance tuning

- Preserve the original batch-16/worker-0/FP32 configuration as the reference.
- Benchmark one variable at a time on identical E7 data before choosing optimized settings; do not infer that the largest batch is best.
- Keep GPU performance benchmarking separate from active training and preserve CPU fallback refusal.
- Use batch 512 FP32 as the current validated E7 throughput configuration. Do not combine it with BF16 for a full run until that combination passes a bounded loss/throughput check.
- Keep loader workers at zero for E7 unless future data transformations materially increase loading time; measured loading and transfer were not the bottleneck.
- Retain Holt-Winters as the validated Phase 4A energy winner and oracle SARIMAX as the peak oracle winner. Treat E6/E7 as underperforming oracle experiments; do not deploy them or infer operational weather value from their accuracy.
- Daily aggregated E7 component quantiles are explicitly uncalibrated at monthly level; use their P50 for point comparison and do not present summed/maximized P10/P90 as calibrated monthly intervals.

- Treat future observed ERA5 variables as oracle covariates only. Oracle results quantify the upper-bound value of perfect weather and are never operational forecasts.
- Use canonical Phase 1C demand targets and Phase 3B weather only over their validated overlap. Translate weather `SYSTEM` to demand `ERCOT_SYSTEM` without changing target definitions.
- Record monthly forecast origins at month start for comparable-observation keys. Daily models train through the prior month end and recursively use their own demand predictions thereafter.
- Gate daily demand completeness against the DST-aware expected 23/24/25 local hours; do not reuse weather completeness as a proxy for demand completeness.
- Aggregate daily energy forecasts by summation. Derive monthly system peak from predicted daily system peaks by maximum; never sum regional peaks or quantiles.
- Limit complete 12-month comparisons to origins through 2025-04 because validated weather ends in 2026-05. Retain the approximate spatial mapping flag as a regional-analysis limitation.
- Run SARIMAX and LightGBM on CPU. Run E6/E7 only on the verified AMD R9700 through ROCm; a missing GPU is a hard failure.
- Parallelize E0-E5 only across independent experiment/target/origin tasks. Use spawned workers rather than `fork` because LightGBM/OpenMP state can deadlock after fork. Bound LightGBM to two threads per worker and native BLAS/OpenMP to one.
- Require exact prediction equality against the sequential bounded run before accepting a worker count. Persist diagnostics before failing the benchmark; do not silently accept floating-point divergence.
- Canonicalize prediction row and column ordering before equality checks because asynchronous task completion legitimately changes concatenation order; do not misclassify ordering-only differences as numerical nondeterminism.
- Construct recursive daily horizons from calendar-period month ends, not timestamp month offsets, so leap days and longer target months are never omitted. Reconcile aggregated-daily actuals to canonical monthly targets within numerical tolerance before evaluation.

## 2026-11-14 — Acquisition safety boundary

- Catalog pages are discovery inputs, never dataset artifacts.
- A file is moved from its temporary path only after HTTP, Content-Type, signature, expected-format, and minimum-parseability checks pass.
- Artifact URLs and filenames come only from authoritative catalog responses; none are synthesized.
- Duplicate URLs and destination filenames are removed during discovery.
- ERCOT historical discovery is restricted to the configured 2004–2026 window; forecast discovery is independent.
- Current FRED series are marked as candidates for revision tracking. Future vintage-aware collection will use ALFRED rather than treating current observations as immutable.
- CDS requests are monthly chunks over the configured U.S. bounds. Credentials remain in local CDS configuration and are never read into logs.
- ERA5 sample parameters are validated and bulk execution still requires explicit `--cds-bulk`. Seasonal remains disabled while its licence/sample gates are incomplete.

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

## 2026-11-14 — Phase 2A baseline design

- Baseline targets are monthly total energy and peak demand for eight weather zones plus an ERCOT system series derived from simultaneous regional sums. EIA retail sales remain separate.
- A 99% configurable completeness threshold controls target eligibility. The 2016-11 period passes that threshold but remains explicitly marked `incomplete_above_threshold`; its missing interval is never synthesized.
- Feature names distinguish `known_future_*` calendar attributes from `historical_*` demand observations. Economic and geopolitical feature stores are prepared but excluded from baseline models.
- LightGBM is a global direct multi-horizon model with geography and horizon features. Each rolling fit uses labels available by its forecast origin; actual intermediate future demand is never used.
- Model selection uses 2018–2019, empirical interval residuals use 2020, and final evaluation begins in 2021. Final-test residuals are never used for uncertainty estimation.
- The requested extreme-weather breakdown is represented by a pre-test calendar-month 95th-percentile peak-load proxy because climate data remains disabled; it is not labeled as a meteorological event classification.
- Holt-Winters is the strongest system baseline in the current test: 4.654% energy WAPE and 5.867% peak WAPE. Empirical interval coverage is materially below the nominal 80%, so uncertainty calibration remains preliminary.

## 2026-11-14 — Phase 2B small TFT

- The isolated EnergyCast environment retains PyTorch `2.13.0+rocm10.1.0` and HIP `7.16.26385`; Lightning and PyTorch Forecasting were added under ROCm constraints without altering either reference environment.
- Strict deterministic execution is unavailable for ROCm `upsample_linear1d_backward`; training uses fixed seeds with Lightning's warning-only deterministic mode and records that limitation.
- TFT decoder inputs contain only static geography and known-future calendar variables. Target-derived lags, rolling values, growth, and peaks are excluded from decoder-known variables.
- The small TFT is retained as the selected neural baseline even though it underperformed Holt-Winters: 18.238% versus 4.654% energy WAPE and 14.712% versus 5.867% peak WAPE.
- Medium and large configurations are not justified by the small model's results and will not be run. A future operational forecast remains gated on a separately tested future-frame procedure that does not fabricate decoder demand observations.
- The final identical-observation audit found no timestamp, target-definition, unit, normalization, inverse-transform, P50-selection, or WAPE-calculation defect. Holt-Winters remains best on the common system sample: 4.597% energy WAPE and 6.042% peak WAPE.
- The main confirmed differences are evaluation-window coverage and training policy: TFT reports complete 12-month decoder windows and keeps weights fixed after the 2004-2017 fit, whereas Phase 2A baselines refit at each origin. Strong negative TFT bias and interval undercoverage are confirmed; explanations involving capacity, regime shift, or covariate limitations remain hypotheses.
- Future decoder targets are placeholders only and are not decoder-known inputs. Predictions were exactly invariant when placeholders changed from 0 to 1,000,000,000, so the future forecast artifact is approved with TFT labeled experimental.

## 2026-11-14 — Phase 3A weather acquisition gate

- Copernicus requests are Texas-only. ERA5 uses the native 0.25° atmospheric grid and monthly chunks; a one-day/four-time sample must pass before any bulk request.
- Seasonal sampling uses ECMWF system 51 and lead months 1–2 initially. Forecast and hindcast data will retain issue time, target time, lead, system, and member and will be labeled separately.
- Catalogue vocabulary validation does not imply authentication or licence acceptance. Both entries remain disabled until those manual account gates and sample validation pass.
- Weather-zone polygons or county mappings will not be reconstructed from a picture. Area-weighted aggregation requires an authoritative machine-readable ERCOT source.

## 2026-11-14 — ERA5 sample normalization

- Preserve CDS ZIP archives and extracted NetCDF members with separate SHA-256 checksums. Cache reuse requires an exact canonical request fingerprint, archive checksum, and member checksums.
- ERA5 reanalysis `tp` and `ssrd` are one-hour accumulations ending at validity time. Do not difference them. Convert precipitation m to mm; retain SSRD in J/m², additionally expose MJ/m² and hourly mean W/m².
- Convert UTC to `America/Chicago` with timezone-aware timestamps. Assign accumulated intervals at local midnight using interval end minus one nanosecond, and compute expected daily counts as 23/24/25 across DST transitions.
- Compute HDD/CDD from each grid cell's Texas-local daily mean using a 65°F base before any spatial aggregation.
- Process one monthly archive at a time and write Zstandard-compressed, year/month-partitioned Parquet. Preserve hourly grid cells; do not duplicate the full history into Zarr unless multidimensional access justifies the extra storage.

## 2026-11-15 — ERA5 alternative provider gate

- Prefer the anonymous AWS Earthmover Icechunk mirror for historical backfill only after orchestration reuses each annual `8736×12×12` spatial chunk across its months. It is the fastest tested provider that matched CDS within float32/GRIB packing precision.
- Preserve the AWS repository snapshot ID on every artifact and partition and reconcile a bounded overlap against CDS. Use CDS for dates newer than the validated AWS snapshot; never silently mix provider versions.
- Do not use Google ARCO as a value-identical canonical source. It was fastest for sparse timestamps but uses global spatial chunks and regridded values differed measurably from CDS.
- Official Copernicus ARCO is an equivalent fallback but requires the local CDS key and was slower than AWS for the tested sparse sample. Its direct Zarr read does not submit a CDS processing request.
- This provider decision does not authorize historical bulk acquisition. January 2004 remains the only complete acquired climate month.

## 2026-11-15 — ERA5 post-acquisition validation

- Treat 2004-01 through 2026-05 as the validated normalized historical-weather range. The final six cached UTC hours on 2026-06-01 exist only to close the May Texas-local accumulated-variable boundary.
- Do not label June 2026 complete until July 1 Texas-local boundary observations are available; October 2026 similarly requires the November 1 boundary. Resume the annual runner rather than redownloading validated 2004–2025 caches.
- Retain and flag the 2,376 source SSRD values of `-0.0014299653 J/m²` at 2004-12-04 05:00 UTC. Their magnitude is negligible, but no correction or clipping is authorized without a documented methodology.
- Do not digitize ERCOT's official JPEG weather-zone map and represent it as authoritative geometry. Continue seeking an official machine-readable polygon or county crosswalk.
- Keep seasonal acquisition disabled until the account has accepted both applicable CDS licence layers and a bounded matching-system forecast/hindcast request validates member, lead-time, issue-date and spatial metadata.

## 2026-11-15 — Phase 3B approximate spatial mapping

- Use the published eight-zone academic county crosswalk plus official Census geometry as a documented secondary approximation only because ERCOT's referenced Attachment C workbook could not be retrieved.
- Exclude counties absent from the crosswalk rather than expanding the ERCOT service area by inference. Preserve partial and coastal cell fractions through EPSG:5070 equal-area intersections.
- Translate source `SOUTH` to the canonical electricity identifier `SOUTHERN`; retain the source typo correction `Houtson` to `Houston` in provenance.
- Regional extremes are extrema across intersecting cells; temperature/humidity/wind/HDD/CDD use area weighting; precipitation and solar radiation are spatially averaged before temporal summation.
- Preserve the 2016 missing demand interval after integration. No interpolation is authorized.
# Decisions
## Phase 5C Stage 2 Gate A

- Use one global direct multi-horizon LightGBM architecture and the same five-variable monthly schema for E18-E20 so provider comparisons change weather inputs rather than model structure.
- Fit supervised historical rows with seasonal-compatible ERA5 weather, but construct E18 forecast rows exclusively from expanding climate normals available at origin. Freeze demand-derived inputs at origin.
- Recompute E0, E11 and non-oracle E14 only by exact-key intersection with E18; exclude all oracle forecasts. Do not interpret the two-horizon smoke metrics as accuracy results.
- Stop after bounded Gate A validation. Full E18 is operator-run; E19-E23 require the next execution gates.

## Phase 5C Stage 2 Gate B

- E19/E20 use raw archived SEAS5/NCEP ensemble means for leads 1-6; do not use chronologically calibrated values because bias correction belongs to E22. Leads 7-12 exactly reuse E18 normals.
- Refit the shared architecture once per target/origin and use that same fitted instance for E19 and E20. Verify deterministic reproduction of the saved E18 P50 before accepting each task.
- Preserve the monthly demand-history origin separately from scheduled operational release time. Lead-1 release occurs within the target month; report this latency and do not describe schedule timestamps as audited publication evidence.
- Compare energy to E14 and peak to E0 on exact common keys. Stop after Gate B; do not start E21-E23.

## Phase 5D structural-bias investigation

- Treat level-target extrapolation under demand-distribution shift as the primary confirmed E18-E20 defect. Recursive compounding, explicit clipping and target normalization are ruled out by implementation/runtime evidence.
- Prioritize E25 because it preserves the strongest Holt-Winters level forecast and learns only strictly out-of-sample residuals. Require development-only selection and retain a no-correction control. Evaluate E24 growth/log-ratio targets second.
- At month start, use normals because neither scheduled provider release is available. Provider weather is eligible only for the mid-month refresh after its scheduled timestamp. These remain simulated schedules rather than audited publication times.
- Chronos-2 future-covariate support is present in the installed implementation, but E26 remains design-only pending a separate ROCm smoke gate. Do not start E21-E27 without review.

## Phase 5D E25

- Train residual correction only from rolling-origin Holt-Winters errors; make each residual label eligible after target-month close plus one day. Never use fitted in-sample residuals.
- Select one shrinkage lambda per target on 2018-2020 development validation, including lambda zero, then hold it fixed. Share one fitted residual model and lambda across A/B/C so operational weather is the experimental change.
- E25-A is a month-start normal-weather forecast. E25-B/C are day-15 refreshes using raw scheduled SEAS5/NCEP leads 1-6 and normals at leads 7-12. No hindcast or future ERA5 feature is permitted.
- Require an extended OOS cache before full execution; do not silently rely on the older cache after its coverage ends. E24/E26/E27 remain unauthorized.
- Retain E25-A as the selected E25 energy configuration and E25-B as the numerical peak winner, with A/B peak performance effectively tied. Seasonal weather does not justify extra complexity over normal-based residual correction.
- E25-B establishes a modest new operational peak benchmark at 6.236% WAPE versus E0 at 6.305%. E14 remains the operational energy benchmark at 4.409%; do not replace it with E25-A at 4.703%.

# Phase 5B seasonal acquisition

- Use ECMWF SEAS5 system 51 as primary and CDS NCEP CFSv2 system 2 as a
  secondary comparison. Exclude NOAA direct CFSv2 because the validated NCEI
  artifact required global-file transfer and lacked a usable subset service.
- Submit one initialization month at a time and one CDS job at a time. Do not
  retry licence/authentication/invalid-request failures.
- Keep retrospective hindcast `available_at` null. Provider publication
  schedules are recorded separately and never converted into synthetic
  historical real-time availability.
- Use seasonal monthly features only at their supported frequency. Do not
  infer daily extremes, heatwaves or cold waves from monthly means. Use labeled
  forecast-origin-safe climate normals for lead months 7–12.
- Retain the 1° grid at native resolution and aggregate with the reusable
  equal-area matrix, explicitly inheriting the approximate ERCOT mapping caveat.
- Preserve both member-level regional values and ensemble P10/P50/P90 summaries.
  For operational 12-month features, use SEAS5 only for supported leads 1–6 and
  expanding ERA5 normals for leads 7–12, fitted strictly before each origin.
  Keep unavailable fallback dewpoint/quantiles null and never infer daily
  extremes from seasonal monthly means.

## Phase 5D E24

- Isolate target transformation with expanding climate normals and identical LightGBM architecture/features across E24 variants; dynamic seasonal weather is excluded.
- E24-A is direct seasonal year-over-year log growth with lambda fixed at 1. E24-C uses the same reference with development-selected shrinkage, isolating shrinkage rather than changing two variables.
- E24-B uses only historical rolling-origin OOS Holt-Winters references. Require positive denominators, reference dates no later than origin and labels available only after target-month close.
- Day-15 climate-normal inputs are identical to month-start inputs; audit the schedule separately without duplicating predictions or claiming a refresh benefit.
- Retain E24-B as the numerical operational peak benchmark (6.201% WAPE), while noting its small, statistically inconclusive improvement over E25-B by horizon band. Retain E14 as the operational energy benchmark (4.409%).
- Do not promote E24-A/C: direct or shrunken seasonal-naive log growth increases extrapolation frequency but worsens WAPE and does not eliminate above-range underforecasting.

## EnergyCast V1 final validation

- Freeze E14, E24-B and E0 before V1 selection. Select on 2018-2019 system WAPE, reserve 2020 OOS residuals solely for probabilistic calibration, and preserve 2021+ as final evaluation.
- Select E14 for V1 energy and E24-B for V1 peak from development criteria. E25-B is not selection-eligible because the 2018-2020 operational seasonal archive needed for an equivalent development evaluation does not exist.
- Calibrate relative, symmetric conformal residual radii separately by target, geography and horizon, falling back only within the same geography. A Jan-Jun/Jul-Dec 2020 calibration-only comparison selected relative scaling over fixed geography-median scaling; final-test results were not used for this choice.
- Treat day-15 output as invariant for V1: neither frozen candidate receives new dynamic weather information after month start. Do not imply a refresh benefit.
- Accept E14 energy and E24-B peak as the validation-selected deterministic V1 models. Their final-test paired improvements over E0 are confirmatory only and were not used for selection.
- Reject the current intervals for deployment: relative symmetric conformal calibration improves coverage over fixed 2020 widths but remains materially below 80% on 2021+. Do not inflate using final-test residuals. Stop deployment pending a pre-test-safe remedy.

## V1 calibration remedy protocol

- Preserve the failed final-test interval result unchanged. Remedy development may read only OOS development predictions ending in 2020 and must not be evaluated against 2021+ during selection.
- Compare predefined scale-normalized symmetric, scale-normalized asymmetric and relative asymmetric methods with fixed 1-3/4-6/7-9/10-12 horizon bands. Require 24 band samples and fall back only within the same geography.
- Select system and regional methods separately using forecast-origin-blocked development folds, normalized pinball loss and a predefined 80%-coverage penalty.
- Because no untouched historical holdout remains, keep intervals disabled after selection and require prospective or independently held-out confirmation before deployment.
- Retain the four selected scope-specific remedy tables as prospective candidates, not validated production intervals. Development coverage misses 80% regionally for energy and substantially exceeds it system-wide for peak; high-growth conditional coverage is unstable.
- Do not assume a constant NCEP ensemble size: observed completed hindcast
  initializations already contain 28, 24 and 20 members. Validate and preserve
  actual membership per initialization. Block official regional processing and
  provider comparison until all 227 priority partitions pass completion gates.
- Freeze deterministic V1 publication to E14 non-oracle energy and E24-B peak.
  Do not expose probabilistic intervals; frozen remedy candidates run only as
  internal shadow outputs until independent prospective confirmation.
- Require an exact-key, 12-horizon frozen candidate bundle whose origin equals
  the validated source cutoff. Do not silently refit historical backtest recipes
  during production inference. This leaves live publication gated until fitted
  artifacts are frozen or forecast-origin refitting is explicitly authorized.
- Keep monthly scheduling preview-only. Publish atomically only after validation,
  retain immutable forecast history, and allow rollback only to validated IDs.
- Select Option B and freeze fitted artifacts once at the approved 2026-10 data
  cutoff. Production inference must load and checksum this bundle; it must never
  refit implicitly. Component-specific OOS-label cutoffs remain explicit rather
  than being misrepresented as the overall demand/context cutoff.
