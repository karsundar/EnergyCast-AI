# Phase 5D execution

## E25 bounded validation

E25 preserves the validated Holt-Winters implementation and trains LightGBM
only on rolling-origin out-of-sample Holt-Winters residuals. Residual labels are
eligible after target-month close plus one day. The residual model is refitted
chronologically at each forecast origin; E25-A/B/C share that fitted model,
hyperparameters and target-specific shrinkage.

Shrinkage is selected on 2018-2020 development validation from
`[0, .25, .5, .75, 1]`, including unchanged Holt-Winters at zero. The bounded
run selected 0.50 for energy and 0.25 for peak. Final test outcomes never select
lambda or model parameters.

Historical residual training weather uses expanding normals available at each
origin. E25-A issues at month start with normals. E25-B/C issue at the defined
day-15 refresh using raw SEAS5/NCEP for leads 1-6 and normals for leads 7-12.
Retrospective hindcasts and future ERA5 are excluded.

Run the bounded validation:

```bash
.venv/bin/python scripts/run_phase5d_e25.py \
  --smoke-test --no-resume --bootstrap-samples 100
```

## Full manual execution

The first full run must extend the strictly OOS Holt-Winters residual cache.
This is the long-running step and was not launched by the agent:

```bash
.venv/bin/python scripts/run_phase5d_e25.py \
  --rebuild-residual-cache \
  --start-origin 2020-12-01 \
  --end-origin 2025-04-01 \
  --max-horizon 12 \
  --bootstrap-samples 1000 \
  2>&1 | tee reports/phase5d_e25_console.log
```

After the extended cache exists, resume forecast partitions without rebuilding:

```bash
.venv/bin/python scripts/run_phase5d_e25.py \
  --start-origin 2020-12-01 \
  --end-origin 2025-04-01 \
  --max-horizon 12 \
  --bootstrap-samples 1000 \
  2>&1 | tee -a reports/phase5d_e25_console.log
```

Evaluate completed partitions without fitting:

```bash
.venv/bin/python scripts/run_phase5d_e25.py --evaluate-only
```

Monitor with:

```bash
tail -f reports/phase5d_e25_console.log
```

Review `reports/phase5d/e25/full/E25_metrics.csv`,
`reports/phase5d/e25/full/E25_paired_differences.csv`,
`reports/phase5d/e25/full/E25_training_range_behavior.csv` and
`reports/phase5d/e25/full/E25_report.json`. E24, E26 and E27 remain unstarted.

## Full E25 result

The operator completed 34,344 predictions and 57,240 common comparison rows.
E25-A is best for energy at 4.703% system WAPE; E25-B is best for peak at
6.236%. Both improve E0 (4.765%/6.305%) and E9 (4.782%/6.545%), while E14
remains the best operational energy forecast at 4.409%. Seasonal weather adds
no material improvement over E25-A normals. See
`reports/phase5d/e25/E25_REVIEW.md`. E24/E26/E27 remain unstarted.

## E24 bounded validation

E24 uses one controlled LightGBM feature schema and expanding climate normals
only. E24-A directly predicts year-over-year log growth (`lambda=1`). E24-C
uses the same seasonal-naive reference with development-selected
multiplicative shrinkage. E24-B predicts a log ratio relative to strictly
rolling-origin OOS Holt-Winters forecasts. Denominators are positive and
observable at origin; labels become eligible only after target-month close.

The bounded smoke passed for both targets, the ERCOT system, one origin and two
horizons: 12 E24 forecasts and an exact 36-row common comparison with E0, E9,
E14 and E25-A/B/C. Leakage and positivity violations were zero. Full evaluation
has not run.

Foreground:

```bash
cd /home/karthiks/src/EnergyCast-AI
.venv/bin/python scripts/run_phase5d_e24.py \
  --start-origin 2020-12-01 --end-origin 2025-04-01 --max-horizon 12
```

Background:

```bash
cd /home/karthiks/src/EnergyCast-AI
nohup .venv/bin/python scripts/run_phase5d_e24.py \
  --start-origin 2020-12-01 --end-origin 2025-04-01 --max-horizon 12 \
  > reports/phase5d_e24_console.log 2>&1 & echo $! > reports/phase5d_e24.pid
```

Monitor and stop safely:

```bash
tail -f /home/karthiks/src/EnergyCast-AI/reports/phase5d_e24_console.log
kill -INT "$(cat /home/karthiks/src/EnergyCast-AI/reports/phase5d_e24.pid)"
```

Resume with the same launch command; completed partitions are skipped. Validate
completed outputs without refitting:

```bash
cd /home/karthiks/src/EnergyCast-AI
.venv/bin/python scripts/run_phase5d_e24.py --evaluate-only
```

Review `reports/phase5d/e24/full/E24_metrics.csv`,
`reports/phase5d/e24/full/E24_paired_differences.csv`,
`reports/phase5d/e24/full/E24_training_range_behavior.csv` and
`reports/phase5d/e24/full/E24_report.json`. E26/E27 remain unstarted.

## Full E24 result

The operator completed 34,344 E24 predictions and 103,032 identical-observation
rows. E24-B is the numerical operational peak winner at 6.201% WAPE versus
E25-B at 6.236% and E0 at 6.305%. Its horizon-band paired intervals do not show
a conclusive improvement over E25-B. E24-B energy WAPE is 4.688%; E14 remains
best at 4.409%. E24-A/C worsen both targets, and above-range bias remains
negative. See `reports/phase5d/e24/E24_REVIEW.md`. E26/E27 remain unstarted.
