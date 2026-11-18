# Phase 4B execution

Phase 4B preserves every Phase 4A artifact. E10 and E12 use observed future
weather and remain explicitly **oracle** experiments.

## E8-E10 CPU experiments

Bounded smoke test (one system origin, two horizons):

```bash
cd /home/karthiks/src/EnergyCast-AI
.venv/bin/python scripts/run_phase4b.py \
  --experiments E8,E9,E10 --smoke-test --workers 2
```

Full manual run (not launched by the agent):

```bash
.venv/bin/python scripts/run_phase4b.py \
  --experiments E8,E9,E10 \
  --start-origin 2020-12-01 --end-origin 2025-04-01 \
  --max-horizon 12 --workers 8
```

Defaults are 8 worker processes, 2 LightGBM threads per worker, and 1
BLAS/OpenMP thread per worker. Override the first two with `--workers` and
`--lightgbm-threads`. E8 and E9 are non-oracle; E10 uses observed future
weather and is explicitly oracle-only.

Residual caches contain historical expanding-window forecasts, not fitted
in-sample residuals. Level versus proportional-growth correction is selected
using 2018-2020 validation data and is fixed before 2021 test evaluation.

Background execution and monitoring:

```bash
mkdir -p logs reports/phase4b
nohup env PYTHONUNBUFFERED=1 .venv/bin/python scripts/run_phase4b.py \
  --experiments E8,E9,E10 --workers 8 --lightgbm-threads 2 \
  --start-origin 2020-12-01 --end-origin 2025-04-01 --max-horizon 12 \
  > logs/phase4b_cpu.log 2>&1 & echo $! > reports/phase4b/cpu.pid
tail -f logs/phase4b_cpu.log
watch -n 5 'cat reports/phase4b/progress.json'
kill -TERM "$(cat reports/phase4b/cpu.pid)"
```

SIGTERM stops scheduling output, preserves every atomic completed partition,
and records an interrupted status. Relaunch the identical `nohup` command to
resume; resume is enabled by default. Use `--no-resume` only to intentionally
replace selected partitions. Existing residual caches are reused; use
`--rebuild-residual-cache` only after a confirmed defect.

Residual targets are expanding-window, out-of-sample forecasts. Before fitting,
the runner verifies every target month is after its forecast origin and excludes
actuals/residual labels from model features. Absolute (`level`) versus percentage
(`growth`) residual correction is selected on development targets ending no
later than December 2020. P10/P90 offsets are also calibrated only from those
development residuals.

After all tasks complete, compare E0-E10 on the exact common observations:

```bash
.venv/bin/python scripts/run_phase4b.py --evaluate-only \
  --comparison-experiments E0,E1,E2,E3,E4,E5,E6,E7,E8,E9,E10
```

Outputs:

- `reports/phase4b/progress.json`: resumable task ledger.
- `reports/phase4b/phase4b_execution.json`: task runtime and peak worker RAM.
- `reports/phase4b/phase4b_metrics_comparable.csv`: WAPE, MAE, RMSE, bias,
  P10-P90 coverage, oracle label, and observation counts.
- `data/processed/phase4b/phase4b_predictions_comparable.parquet`: exact common
  E0-E10 observations.

## Chronos-2 isolated environment

Chronos-2 is a 120M-parameter model with native known-future covariate support.
The model weights are downloaded from Hugging Face on the first invocation.
Do not install Chronos into the working EnergyCast environment.

```bash
cp -a .venv .venv-chronos2
.venv-chronos2/bin/python -m pip install --dry-run \
  -c constraints-phase2b-rocm.txt -r requirements-chronos2.txt
.venv-chronos2/bin/python -m pip install \
  -c constraints-phase2b-rocm.txt -r requirements-chronos2.txt
.venv-chronos2/bin/python -m pip check
```

Bounded zero-shot smoke tests:

```bash
.venv-chronos2/bin/python scripts/run_phase4b_chronos.py \
  --experiment E11 --target total_energy_mwh --smoke-test
.venv-chronos2/bin/python scripts/run_phase4b_chronos.py \
  --experiment E12 --target total_energy_mwh --smoke-test
```

Fine-tuning is double-gated and is not part of the initial smoke run:

```bash
.venv-chronos2/bin/python scripts/run_phase4b_chronos.py \
  --experiment E13 --target total_energy_mwh \
  --smoke-test --allow-fine-tune
```

Remove `--smoke-test` only after reviewing zero-shot accuracy, runtime and VRAM.
Run each target separately. The model revision is pinned in `config/phase4b.json`.

## E14 ensemble and diagnostics

E14 refuses to learn weights without a separate validation prediction artifact:

```bash
.venv/bin/python scripts/run_phase4b_ensemble.py \
  --validation-predictions data/processed/phase4b/validation_predictions.parquet
.venv/bin/python scripts/analyze_phase4b_bias.py
```

Evaluate completed test partitions with the existing comparable-observation
evaluator only after all selected experiments finish:

```bash
.venv/bin/python scripts/run_phase4a.py --evaluate-only
```
