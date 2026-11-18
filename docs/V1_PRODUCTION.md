# EnergyCast V1 deterministic production

## Release state

The deterministic publication framework is implemented and its historical
replay gate passes exactly. Production intervals remain disabled. Frozen
research calibration candidates are written only to an internal shadow file
and carry no validated-probability claim.

Option B is complete. The independently loadable fitted bundle is frozen at
`models/production/energycast_v1_202610`; the production runner validates every
artifact checksum and performs inference without fitting. Live publication and
unattended scheduling remain intentionally inactive.

## Commands

Bounded historical replay and dry run:

```bash
cd /home/karthiks/src/EnergyCast-AI
.venv/bin/python scripts/run_production_v1.py prepare-replay --origin 2025-04-01
.venv/bin/python scripts/run_production_v1.py run --dry-run \
  --as-of 2025-04-30 --issued-at 2025-05-01T06:15:00Z
```

Refresh the ERCOT cache and rebuild normalized Phase 1C data. This is a network
operation and remains manual:

```bash
.venv/bin/python scripts/run_production_v1.py refresh
```

Once a frozen live candidate bundle exists at
`data/production/v1/input/candidate_forecasts.parquet`, publish and validate:

```bash
.venv/bin/python scripts/run_production_v1.py run
.venv/bin/python scripts/run_production_v1.py validate
.venv/bin/python scripts/run_production_v1.py monitor
```

Strict fitted-bundle validation and an inference-only dry run:

```bash
.venv/bin/python scripts/run_production_v1.py validate-bundle
.venv/bin/python scripts/validate_production_v1_bundle.py \
  --bundle models/production/energycast_v1_202610
.venv/bin/python scripts/run_production_v1.py run --dry-run
```

Rollback to an immutable validated forecast:

```bash
.venv/bin/python scripts/run_production_v1.py rollback --forecast-id FORECAST_ID
```

Preview, but do not install, the monthly schedule:

```bash
.venv/bin/python scripts/run_production_v1.py schedule-preview
```

The preview uses `15 6 2 * *` in `America/Chicago`. Unattended activation is
not authorized.

## Safety behavior

- Source freshness, nine-geography completeness and the configured completeness
  threshold are checked before inference.
- Candidate origin must equal the validated data cutoff.
- E14 requires all four fixed-weight components; E24-B peak must be present.
- Every target/geography must contain exactly horizons 1–12.
- Public output contains no P10/P90 or confidence fields.
- History is immutable and current output changes only after full validation.
- Identical input/model/vintage combinations have a deterministic forecast ID.
- Refresh restores the prior normalized monthly target if rebuilding fails.
- Monitoring joins only actual observations that have subsequently become
  available; it never retunes the frozen shadow calibration.

## Validation evidence

The frozen 2026-10 bundle produced 216 forecasts in each of two fresh processes
with maximum absolute difference `0.0`; all 216 values were finite. Four focused
production tests passed, including corrupt-artifact rejection. A simulated
failure left the prior dry-run forecast checksum unchanged. Scheduler output is
preview-only. No live publication was performed.
