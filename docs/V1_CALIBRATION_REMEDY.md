# EnergyCast V1 calibration remedy

This workflow does not read or overwrite the frozen 2021+ final-test results.
It operates only on OOS development predictions ending in December 2020.
Deterministic E14 energy and E24-B peak forecasts remain enabled and unchanged;
probabilistic intervals remain disabled.

## Candidate methods

1. Geography-scale-normalized symmetric conformal intervals.
2. Geography-scale-normalized asymmetric residual quantiles.
3. Relative asymmetric residual quantiles.

All methods use fixed horizon bands 1-3, 4-6, 7-9 and 10-12. A band requires
24 observable residuals; otherwise calibration falls back only within the same
target/geography. System and regional methods are selected separately.

Blocked folds are 2019-H1, 2019-H2, 2020-H1 and 2020-H2. For each fold, a
residual enters calibration only when its target month has closed before the
fold's first forecast origin. Evaluation reports coverage, interval width and
pinball loss overall, by horizon band, geography, origin block and high-growth
regime.

## Bounded test

```bash
cd /home/karthiks/src/EnergyCast-AI
.venv/bin/python scripts/run_v1_calibration_remedy.py --smoke-test
```

The bounded run reads zero final-test rows, has zero future-cutoff violations
and leaves interval serving disabled.

## Full development-only evaluation

```bash
cd /home/karthiks/src/EnergyCast-AI
.venv/bin/python scripts/run_v1_calibration_remedy.py
```

Optional background execution:

```bash
cd /home/karthiks/src/EnergyCast-AI
nohup .venv/bin/python scripts/run_v1_calibration_remedy.py \
  > reports/v1_calibration_remedy_console.log 2>&1 & \
  echo $! > reports/v1_calibration_remedy.pid
```

Monitor or stop:

```bash
tail -f /home/karthiks/src/EnergyCast-AI/reports/v1_calibration_remedy_console.log
kill -INT "$(cat /home/karthiks/src/EnergyCast-AI/reports/v1_calibration_remedy.pid)"
```

Review `reports/v1/calibration_remedy/full/calibration_remedy_report.json`,
`selected_methods.csv`, `method_comparison.csv` and `blocked_metrics.csv`.

No genuinely untouched historical holdout remains after method development.
The selected remedy must therefore be labeled **awaiting prospective or
independently held-out confirmation**. Do not apply it to the existing final
test or enable probabilistic intervals for deployment.

## Full development result

The four-fold run completed with 11,340 candidate predictions and no final-test
reads or cutoff violations. Selected development coverage is 80.5% energy
system, 74.2% energy regional, 91.9% peak system and 80.8% peak regional.
Coverage varies by origin block and horizon, and high-growth subsets remain
materially undercovered. Frozen tables are research candidates only;
probabilistic serving remains disabled. See
`reports/v1/calibration_remedy/CALIBRATION_REMEDY_REVIEW.md`.
