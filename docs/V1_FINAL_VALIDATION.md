# EnergyCast V1 final validation

## Frozen selection protocol

- Candidate configurations are frozen before this phase.
- System-level model selection uses 2018-01 through 2019-12 development rows.
- Scale-aware interval calibration uses only 2020 OOS residuals.
- Final evaluation begins in 2021 and does not influence selection or calibration.
- Errors are normalized relative to each forecast without pooling geographies.
  Symmetric conformal radii are estimated by
  target, geography and horizon where at least eight samples exist, with a
  same-geography fallback. The method was selected using a Jan-Jun 2020 fit and
  Jul-Dec 2020 holdout; unscaled errors are never pooled across regions.

The bounded gate selects E14 for energy (2.969% development WAPE) and E24-B for
peak (4.320%). E25-B remains a final-test challenger rather than an eligible
selector because equivalent archived operational seasonal forecasts do not
exist across the 2018-2020 development window.

Both frozen V1 candidates are valid at month start. A day-15 run produces the
same result because E14 is weather-independent and E24-B uses expanding climate
normals; no simulated refresh benefit is claimed. Neither candidate consumes
future ERA5 observations or future demand.

## Bounded validation

```bash
cd /home/karthiks/src/EnergyCast-AI
.venv/bin/python scripts/run_v1_final_validation.py --smoke-test
```

## Full final evaluation

Foreground:

```bash
cd /home/karthiks/src/EnergyCast-AI
.venv/bin/python scripts/run_v1_final_validation.py --bootstrap-samples 1000
```

Background:

```bash
cd /home/karthiks/src/EnergyCast-AI
nohup .venv/bin/python scripts/run_v1_final_validation.py \
  --bootstrap-samples 1000 \
  > reports/v1_final_validation_console.log 2>&1 & \
  echo $! > reports/v1_final_validation.pid
```

Monitor:

```bash
tail -f /home/karthiks/src/EnergyCast-AI/reports/v1_final_validation_console.log
```

Stop safely if needed:

```bash
kill -INT "$(cat /home/karthiks/src/EnergyCast-AI/reports/v1_final_validation.pid)"
```

The full command is deterministic post-processing and can be rerun directly.
It does not train E26/E27 or modify existing checkpoints. Review outputs under
`reports/v1/full/` and calibrated predictions under `data/processed/v1/full/`.

Deployment work remains gated until the full report is reviewed.

## Full evaluation result

The full deterministic evaluation confirms E14 energy at 4.409% WAPE and E24-B
peak at 6.201%. All operational and leakage gates pass. Probabilistic coverage,
however, is below the 80% target: 53.5% energy / 57.7% peak system-wide and
69.1% / 65.4% regionally. Do not inflate intervals from final-test outcomes.
Deployment remains blocked pending review of a pre-test-safe calibration remedy.
See `reports/v1/V1_FINAL_REVIEW.md`.

The separate pre-test-safe remedy workflow is documented in
`docs/V1_CALIBRATION_REMEDY.md`. It does not overwrite these final-test results
and cannot enable intervals without prospective or independent confirmation.
