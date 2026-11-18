# Phase 4C execution

Phase 4C preserves all Phase 4A/4B artifacts. E12 is oracle because it uses
observed future weather. E11, E13, E15, E16, and E17 are non-oracle when their
base forecasts are non-oracle. E14 must be run separately for non-oracle and
oracle-mixed candidate sets.

## Validated bounded CPU preparation

```bash
cd /home/karthiks/src/EnergyCast-AI
.venv/bin/python scripts/build_phase4c_validation.py
.venv/bin/python scripts/run_phase4b_ensemble.py \
  --candidates E0,E8 --mode non-oracle --smoke-test
.venv/bin/python scripts/run_phase4c_postprocess.py --experiment E15 --smoke-test
.venv/bin/python scripts/run_phase4c_postprocess.py --experiment E16 --smoke-test
.venv/bin/python scripts/run_phase4b.py \
  --experiments E17 --smoke-test --workers 1 --no-resume
```

The validation artifact contains only OOS forecasts whose target dates are in
2018-2020. Calibration fits pre-2020 data and selects relative versus
scale-normalized residuals on the 2020 holdout. Groups are target, geography,
and horizon where at least 12 samples exist, with target-level fallback.

Current bounded holdout coverage is approximately 56-58%, below the nominal
80% target. Do not describe E16 as calibrated until full evaluation confirms it.

## Isolated Chronos-2 environment

```bash
cp -a .venv .venv-chronos2
.venv-chronos2/bin/python -m pip install --dry-run \
  -c constraints-phase2b-rocm.txt -r requirements-chronos2.txt
.venv-chronos2/bin/python -m pip install \
  -c constraints-phase2b-rocm.txt -r requirements-chronos2.txt
.venv-chronos2/bin/python -m pip check
.venv-chronos2/bin/python -c \
  "import torch,chronos; print(torch.__version__,torch.version.hip,torch.cuda.is_available(),torch.cuda.get_device_name(0))"
```

Benchmark identical bounded E11 inference before choosing settings:

```bash
.venv-chronos2/bin/python scripts/run_phase4b_chronos.py \
  --experiment E11 --target total_energy_mwh --smoke-test \
  --precision fp32 --benchmark-batch-sizes 32,64,128,256
.venv-chronos2/bin/python scripts/run_phase4b_chronos.py \
  --experiment E11 --target total_energy_mwh --smoke-test \
  --precision bf16 --benchmark-batch-sizes 32,64,128,256
```

Each benchmark records inference duration and peak allocated VRAM. Select the
fastest finite configuration; do not assume the largest batch is best.

Measured on the AMD R9700 bounded E11 energy smoke: BF16 used 272,952,320 peak
allocated bytes versus 512,094,208 for FP32. Post-warm-up BF16 batch 64 took
0.0127 seconds. Because smoke mode contains only one series, tested batches
32-256 exceed workload cardinality; BF16/64 is provisional and should be
rechecked on one all-region origin before full fine-tuning.

Bounded E11-E13 smoke tests, using the selected batch and precision:

```bash
.venv-chronos2/bin/python scripts/run_phase4b_chronos.py \
  --experiment E11 --target total_energy_mwh --smoke-test --batch-size 64 --precision fp32
.venv-chronos2/bin/python scripts/run_phase4b_chronos.py \
  --experiment E12 --target total_energy_mwh --smoke-test --batch-size 64 --precision fp32
.venv-chronos2/bin/python scripts/run_phase4b_chronos.py \
  --experiment E13 --target total_energy_mwh --smoke-test --allow-fine-tune \
  --batch-size 64 --precision fp32
```

Repeat E13 smoke with the best BF16 setting if its benchmark is finite. Only
then remove `--smoke-test`. Run energy and peak separately. For E11-E13
validation predictions, add `--validation-run`; those partitions remain
separate from final-test partitions.

ROCm compatibility note: the runner explicitly passes `tf32=False` to Chronos
fine-tuning. Chronos 2.3.0 otherwise interprets the ROCm CUDA-compatible Python
interface as an NVIDIA SM80 device and Transformers rejects TF32 on AMD. BF16
is enabled explicitly only when `--precision bf16` is selected.

## Manual full execution

E11-E13 (example E11; repeat experiment and target explicitly):

```bash
nohup env PYTHONUNBUFFERED=1 .venv-chronos2/bin/python scripts/run_phase4b_chronos.py \
  --experiment E11 --target total_energy_mwh --batch-size 64 --precision fp32 \
  > logs/phase4c_E11_energy.log 2>&1 & echo $! > reports/phase4b/E11_energy.pid
tail -f logs/phase4c_E11_energy.log
kill -TERM "$(cat reports/phase4b/E11_energy.pid)"
```

E17 resumable CPU run:

```bash
nohup env PYTHONUNBUFFERED=1 .venv/bin/python scripts/run_phase4b.py \
  --experiments E17 --workers 8 --lightgbm-threads 2 \
  --start-origin 2020-12-01 --end-origin 2025-04-01 --max-horizon 12 \
  > logs/phase4c_E17.log 2>&1 & echo $! > reports/phase4b/E17.pid
tail -f logs/phase4c_E17.log
kill -TERM "$(cat reports/phase4b/E17.pid)"
```

E14-E16 after validation and test forecasts exist:

```bash
.venv/bin/python scripts/run_phase4b_ensemble.py \
  --candidates E0,E8,E9,E11,E13 --mode non-oracle
.venv/bin/python scripts/run_phase4b_ensemble.py \
  --candidates E0,E1,E8,E9,E10,E11,E12,E13 --mode oracle-mixed
.venv/bin/python scripts/run_phase4c_postprocess.py --experiment E15
.venv/bin/python scripts/run_phase4c_postprocess.py --experiment E16
.venv/bin/python scripts/analyze_phase4b_bias.py
```

The analysis reports bias by year, geography, season, and horizon. Full
identical-observation evaluation must include only experiments with complete
partitions and must keep oracle and non-oracle rankings separate.

## Closure (2026-11-15)

- E13: smoke and validation complete; full fine-tuning deferred because it did
  not materially improve E11 validation.
- E15: complete. System energy WAPE is 2.883% on its evaluation set, but peak
  WAPE is 6.773%; negative bias remains.
- E16: complete. Validation-only scale calibration plus a 2020 holdout reaches
  80.8% energy and 85.5% peak system P10-P90 coverage. Regional coverage is
  68.7% and 75.9%; it was not corrected using test residuals.
- E17: smoke complete; full rolling backtest pending and excluded from rankings.

Final leaders are E14 oracle at 3.247% energy / 5.403% peak WAPE, E14
non-oracle energy at 4.409%, and E0 non-oracle peak at 6.305%.
