# Phase 5C execution

## Stage 1 implementation

The Stage 1 evaluator compares ECMWF SEAS5 system 51 and CDS NCEP CFSv2
system 2 with the canonical regional ERA5 monthly observations on exact target
month, ERCOT region and lead. It evaluates temperature, relative humidity,
wind speed, precipitation and solar energy in the existing canonical units.
ERA5 monthly regional data do not contain dewpoint, so dewpoint is not scored.

Archived forecasts and retrospective hindcasts are isolated. Archived rows must
have a non-null recorded `available_at`; hindcasts must have null availability.
Target month must equal initialization month plus `lead - 1`. The absent
2017–2020 archive remains absent. Hindcast metrics and calibration are labeled
research-only and cannot be used as operational results.

Expanding ERA5 climate normals use the same calendar month and only years before
the forecast initialization, with a ten-year minimum. Chronological bias
correction uses only prior errors whose verifying ERA5 month would have been
observable before the forecast availability. The conservative configured ERA5
availability assumption is month end plus seven days. This assumption must be
retained in result provenance.

Provider weights use inverse MAE from 2021–2023 archived validation only for
operational results. A separate 2012–2014 hindcast research-validation split is
used for the retrospective experiment. The 2024–2026 archived test and
2015–2017 hindcast research-test periods never select weights. Metrics include
MAE, RMSE, bias, anomaly correlation, P10–P90 coverage and interval width by
provider, variable, lead, system/regional scope and evaluation period.

The bounded smoke run passed on 5,832 matched rows and all six leads. It found
zero archived rows without availability, zero hindcasts with synthetic
availability and zero fabricated archived rows in 2017–2020. Smoke outputs are
diagnostic only and must not be reported as full skill results.

## Commands

Run the bounded smoke check:

```bash
.venv/bin/python scripts/run_phase5c_weather_skill.py --smoke-test
```

Run the complete Stage 1 evaluation (expected to take well under five minutes;
it performs no model training and no network access):

```bash
.venv/bin/python scripts/run_phase5c_weather_skill.py \
  --config config/phase5c_weather_skill.json \
  2>&1 | tee reports/phase5c_full_console.log
```

Inspect completion and key outputs:

```bash
cat reports/phase5c/full/validation.json
.venv/bin/python -c "import pandas as pd; p='reports/phase5c/full/weather_skill_metrics.parquet'; d=pd.read_parquet(p); print(d.groupby(['track','period','provider','variable','lead_month'])[['mae','rmse','bias','anomaly_correlation','p10_p90_coverage']].first().to_string())"
```

The run is atomic at the file level. If interrupted, rerun the same command;
it reads immutable Phase 5B products and replaces only completed Stage 1 report
files. No background/nohup workflow is necessary for this bounded evaluation.

## Review gate

Stage 2 experiments E18–E23 are not implemented or launched yet. Review the
full Stage 1 archived-test results, provider calibration, ensemble weights and
reliability before selecting seasonal inputs for electricity forecasting.
Leads 7–12 will use explicitly labeled expanding climate normals; monthly
seasonal products cannot supply daily extremes.

The full review found a spatial wind-definition defect in the initial output.
The original report is preserved. Build versioned corrected inputs and rerun to
an independent output directory with:

```bash
.venv/bin/python scripts/rebuild_phase5c_seasonal_wind.py
.venv/bin/python scripts/run_phase5c_weather_skill.py \
  --config config/phase5c_weather_skill.json --output-label full_v2
```

The corrected run completed on 23,598 rows. Only calibrated NCEP temperature
improved over expanding normals system-wide. Wind and humidity remain
proxy-only because monthly seasonal U/V and temperature/dewpoint cannot exactly
reproduce hourly-derived ERA5 scalar wind and humidity definitions. See
`reports/phase5c/PHASE5C_STAGE1_REVIEW.md` before authorizing Stage 2.

## Stage 2 Gate A — E18 climate normals

Gate A implements a global direct multi-horizon LightGBM model using the five
validated monthly weather fields. Historical training rows use matching
seasonal-compatible ERA5 observations; forecast rows use expanding climate
normals computed using only prior years. All demand lags and rolling values are
frozen at the forecast origin. No daily extrema, HDD/CDD, heatwave or observed
future-weather fields are present.

The bounded system-only smoke run covers one origin and horizons 1–2. It
produced four E18 forecasts and 16 exactly aligned, non-oracle comparison rows
across E0, E11, E14 and E18. These two-observation-per-target metrics validate
the pipeline only and are not accuracy estimates. E19–E23 remain deferred.

Run the bounded smoke validation:

```bash
.venv/bin/python scripts/run_phase5c_stage2.py --smoke-test --no-resume
```

Run the complete Gate A rolling-origin evaluation manually:

```bash
.venv/bin/python scripts/run_phase5c_stage2.py \
  --start-origin 2020-12-01 \
  --end-origin 2025-04-01 \
  --max-horizon 12 \
  2>&1 | tee reports/phase5c_stage2_gate_a_console.log
```

The default is resumable and atomically checkpoints every target/origin. To
resume, run the same command. To evaluate existing partitions without fitting:

```bash
.venv/bin/python scripts/run_phase5c_stage2.py --evaluate-only
```

Review `reports/phase5c_stage2/gate_a/gate_a_report.json` and
`reports/phase5c_stage2/gate_a/gate_a_common_metrics.csv` before Gate B.

### Gate A full result

The operator completed all 11,448 E18 predictions. Exact-key comparison contains
45,792 rows across E0, E11, E14 and E18, with zero oracle rows. System-wide E18
WAPE is 11.830% for energy and 11.443% for peak, versus 4.409% and 6.542% for
non-oracle E14 on the identical observations. E18 has large negative signed
bias (-4.393 million MWh energy and -7,246 MW peak per forecast), consistent
with the previously observed inability of level-based tree models to
extrapolate demand growth. No leakage or alignment defect was found.

Gate B remains useful only as a controlled test of whether dynamic SEAS5/NCEP
inputs improve this fixed weather-aware architecture relative to normals. E18
does not replace the validated weather-independent benchmarks.

## Stage 2 Gate B — E19/E20 raw seasonal weather

E19 and E20 reuse E18's historical training rows, feature schema, LightGBM
hyperparameters and direct multi-horizon protocol. At each target/origin the
model is refitted once using labels ending at the demand-history origin; that
single fitted instance predicts both E19 and E20. E19 uses raw SEAS5 ensemble
means and E20 uses raw NCEP ensemble means for leads 1–6. Both use the identical
E18 expanding-normal values for leads 7–12. No calibrated provider values are
used; calibration remains reserved for E22.

The monthly `origin` field remains the prior demand-history cutoff used by all
comparison models. `weather_available_at` and `operational_forecast_time`
record the scheduled provider release used for the operational simulation.
Consequently, lead-1 weather becomes available on the documented release day
inside its target month (day 6 for SEAS5 and day 10 for NCEP), rather than at
the prior-month demand cutoff. These are schedule-based timestamps, not audited
file publication times, and this latency must accompany Gate B results.

The bounded Gate B smoke test passed. For both targets/providers, all ten
dynamic weather cells changed, unrelated model features changed by exactly
zero, climate-normal fallback changed by zero, and the reconstructed model
reproduced saved E18 P50 predictions exactly. Run it with:

```bash
.venv/bin/python scripts/run_phase5c_stage2.py \
  --gate B --smoke-test --no-resume
```

Run the complete Gate B evaluation manually:

```bash
.venv/bin/python scripts/run_phase5c_stage2.py \
  --gate B \
  --start-origin 2020-12-01 \
  --end-origin 2025-04-01 \
  --max-horizon 12 \
  2>&1 | tee reports/phase5c_stage2_gate_b_console.log
```

The run atomically checkpoints every experiment/target/origin and resumes by
default. Re-run the same command after interruption. Evaluate completed
partitions without refitting with:

```bash
.venv/bin/python scripts/run_phase5c_stage2.py --gate B --evaluate-only
```

Review `reports/phase5c_stage2/gate_b/gate_b_common_metrics.csv`,
`reports/phase5c_stage2/gate_b/weather_feature_difference_audit.parquet` and
`reports/phase5c_stage2/gate_b/gate_b_report.json`. E21–E23 remain deferred.

### Gate B full result

The full run completed 22,896 E19/E20 predictions and 45,792 exact-key common
comparison rows. System energy WAPE is 11.581% for E19, 11.830% for E18 and
11.905% for E20, versus 4.409% for E14. System peak WAPE is 11.303% for E19,
11.443% for E18 and 12.033% for E20, versus 6.305% for E0. SEAS5 therefore
provides a small improvement over normals; NCEP worsens both targets. None
resolves the dominant negative bias or approaches the weather-independent
benchmarks.

For all-geography leads 1–6, E19 improves energy WAPE from 9.450% to 9.197% and
peak WAPE from 10.039% to 9.950%. E20 records 9.757% and 10.836%. Leads 7–12
are exactly identical by design because all three experiments use expanding
normals. The full feature audit confirms zero unrelated-feature difference,
zero fallback-weather difference and exact reproduction of E18 predictions.
E21–E23 remain unstarted pending review.

## Phase 5D bounded structural-bias investigation

The read-only diagnostic is implemented in `scripts/run_phase5d_diagnostics.py`.
It analyzes saved E18-E20 predictions, historical target ranges, growth regimes,
horizons, seasons, regions and paired provider effects. It also audits
month-start versus mid-month weather availability and writes design-only E24-E27
specifications. It does not fit a model.

Run the bounded smoke check:

```bash
.venv/bin/python scripts/run_phase5d_diagnostics.py \
  --smoke-test --bootstrap-samples 100
```

Regenerate the complete read-only diagnostics:

```bash
.venv/bin/python scripts/run_phase5d_diagnostics.py \
  --bootstrap-samples 1000
```

The primary confirmed cause is level-target extrapolation under structural
demand growth. See `reports/phase5d/STRUCTURAL_BIAS_REPORT.md`. E25 is the
recommended first experiment, followed by E24. No E21-E27 run is authorized.
