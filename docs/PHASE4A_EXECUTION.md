# Phase 4A manual execution

Phase 4A measures the value of perfect future weather. E1, E3, E5, E6 and E7 use observed future ERA5 weather and are **oracle experiments**, not operational forecasts. Future electricity demand is never a model input.

## Validated smoke tests

- E0-E5: one ERCOT-system origin and one common monthly horizon passed; 12/12 target/experiment predictions matched on identical observations.
- E6 monthly TFT: one GPU epoch passed on the AMD R9700; validation loss finite; 38,845 parameters.
- E7 daily TFT: one GPU epoch passed on the AMD R9700; validation loss finite; 38,932 parameters.
- Smoke artifacts and progress are isolated under `data/processed/phase4a/smoke/` and `reports/phase4a/smoke/`.

## CPU experiments E0-E5

Foreground:

```bash
cd /home/karthiks/src/EnergyCast-AI
.venv/bin/python scripts/run_phase4a.py \
  --experiments E0,E1,E2,E3,E4,E5 \
  --start-origin 2020-12-01 \
  --end-origin 2025-04-01 \
  --max-horizon 12 \
  --workers 8 \
  --lightgbm-threads 2
```

Background:

```bash
cd /home/karthiks/src/EnergyCast-AI
mkdir -p logs reports/phase4a
nohup .venv/bin/python scripts/run_phase4a.py \
  --experiments E0,E1,E2,E3,E4,E5 \
  --start-origin 2020-12-01 \
  --end-origin 2025-04-01 \
  --max-horizon 12 \
  --workers 8 \
  --lightgbm-threads 2 \
  > logs/phase4a_cpu.log 2>&1 & echo $! > reports/phase4a/cpu.pid
```

Monitor:

```bash
tail -f logs/phase4a_cpu.log
cat reports/phase4a/progress.json
```

Stop safely (the runner persists the current completed partition on `SIGTERM`):

```bash
kill -TERM "$(cat reports/phase4a/cpu.pid)"
```

Resume with the same foreground or background command; resume is enabled by default and validated partitions are skipped.

The default is eight spawned worker processes. Each worker uses at most two
LightGBM threads and one BLAS/OpenMP thread. `spawn` is intentional: forking an
already initialized LightGBM/OpenMP runtime caused idle-worker deadlock during
the first benchmark attempt.

Benchmark deterministic scaling on the fixed bounded workload:

```bash
.venv/bin/python scripts/run_phase4a.py \
  --benchmark-workers 1,4,8,16 \
  --lightgbm-threads 2
```

The benchmark saves timings, prediction hashes, exact sequential-consistency
checks and worker peak RAM to `reports/phase4a/multiprocessing_benchmark.json`.
This workload is deliberately tiny, so process startup can outweigh parallel
speedup; use it as a correctness and resource gate, not a full-run timing model.

Evaluate already completed CPU partitions without training:

```bash
.venv/bin/python scripts/run_phase4a.py --evaluate-only
```

Evaluate the bounded smoke artifacts only:

```bash
.venv/bin/python scripts/run_phase4a.py --smoke-test --evaluate-only
```

The normal evaluator intentionally fails until the full E0-E5 command has created
`data/processed/phase4a/predictions/`. Smoke partitions are isolated and are never
treated as full backtest results.

## GPU experiments E6-E7

Run one target at a time so checkpoints and logs remain independently resumable:

```bash
cd /home/karthiks/src/EnergyCast-AI
.venv/bin/python scripts/run_phase4a_tft.py --experiment E6 --target total_energy_mwh
.venv/bin/python scripts/run_phase4a_tft.py --experiment E6 --target peak_demand_mw
.venv/bin/python scripts/run_phase4a_tft.py --experiment E7 --target total_energy_mwh
.venv/bin/python scripts/run_phase4a_tft.py --experiment E7 --target peak_demand_mw
```

Example background launch:

```bash
nohup .venv/bin/python scripts/run_phase4a_tft.py \
  --experiment E6 --target total_energy_mwh \
  > logs/phase4a_E6_energy.log 2>&1 & echo $! > reports/phase4a/E6_energy.pid
```

Monitor or stop:

```bash
tail -f logs/phase4a_E6_energy.log
kill -TERM "$(cat reports/phase4a/E6_energy.pid)"
```

Resume from `last.ckpt`:

```bash
.venv/bin/python scripts/run_phase4a_tft.py \
  --experiment E6 --target total_energy_mwh --resume
```

### E7 GPU throughput tuning

Do not run the benchmark concurrently with TFT training. The reference configuration
(batch 16, zero loader workers, FP32, deterministic warnings) remains unchanged.
After the current training process exits, run the bounded one-variable-at-a-time sweep:

```bash
cd /home/karthiks/src/EnergyCast-AI
.venv/bin/python scripts/benchmark_phase4a_tft.py \
  --target total_energy_mwh \
  --suite full \
  --warmup-batches 5 \
  --measure-batches 20
```

The sweep tests batch sizes 32/64/128/256/512, workers 0/4/8/16,
pinning, persistent workers, prefetch 2/4, FP32/BF16, and deterministic
modes while changing one setting at a time. It records loading, host-to-device,
forward, backward and optimizer timing plus throughput, RAM, VRAM, GPU use and
projected epoch duration in `reports/phase4a/tft_performance_benchmark.json`.
Oversized batches are reported as out-of-memory and the sweep continues.

Once the measured winner is known, pass its settings explicitly. Example shape
only—replace values with the benchmark result:

```bash
.venv/bin/python scripts/run_phase4a_tft.py \
  --experiment E7 --target peak_demand_mw \
  --batch-size 128 --num-workers 8 \
  --persistent-workers --pin-memory --prefetch-factor 4 \
  --precision bf16-mixed --deterministic warn
```

All performance flags are optional, so existing commands and checkpoints retain
their original behavior. `--resume` should only be used with the same architecture
and compatible precision settings used to create the checkpoint.

The full CPU evaluation must complete before weather-related improvements are reported. Do not describe any oracle result as deployable accuracy. The approximate Phase 3B county mapping remains a limitation for every regional weather experiment.
