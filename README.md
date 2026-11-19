# EnergyCast AI

Local, reproducible electricity-demand forecasting for ERCOT. EnergyCast builds
validated demand and weather datasets, compares statistical, gradient-boosted
and foundation-model forecasts, and packages the selected deterministic models
for guarded production inference.

Repository: [github.com/karsundar/EnergyCast-AI](https://github.com/karsundar/EnergyCast-AI)

Technical blog: [karsundar.github.io/EnergyCast-AI](https://karsundar.github.io/EnergyCast-AI/)

> Status: deterministic V1 is implemented and validated, but no live forecast
> has been published and unattended scheduling is disabled. Probabilistic
> intervals remain research-only pending independent prospective validation.

## V1 results

Models were selected using chronological development validation and evaluated
on identical observations. Oracle experiments use observed future weather only
to measure its theoretical value; they are never treated as operational.

| Forecast target | Selected operational model | System WAPE |
|---|---|---:|
| Monthly ERCOT energy | E14 non-oracle validation-weighted ensemble | **4.409%** |
| Monthly ERCOT peak demand | E24-B Holt-Winters log-ratio LightGBM | **6.201%** |

Useful references:

- Holt-Winters operational energy: 4.766% WAPE.
- E25-B seasonal-weather peak challenger: 6.236% WAPE.
- Best oracle energy: E14, 3.247% WAPE.
- Best oracle peak: E14, 5.403% WAPE.

The calibrated P10-P90 candidates are deliberately not exposed as production
probabilities. Development-only coverage was not uniformly reliable across
system and regional series.

## What is implemented

### Data acquisition and normalization

- ERCOT hourly load and long-term forecast workbooks.
- EIA-861M historical electricity sales and revenue.
- FRED economic series, New York Fed GSCPI and geopolitical risk indicators.
- ERA5 hourly Texas weather from 2004 through May 2026.
- ECMWF SEAS5 system 51 and CDS NCEP CFSv2 system 2 seasonal forecasts and
  retrospective hindcasts.
- Atomic downloads, checksums, cache reuse, retries, response-signature checks,
  resume ledgers and schema validation.
- Canonical ERCOT hourly, daily and monthly Parquet datasets.

### Weather integration

- ERA5 aggregation to eight ERCOT weather zones plus the ERCOT system.
- Area-weighted spatial mapping with documented approximate boundary provenance.
- Temperature, humidity, wind, precipitation, solar, HDD/CDD and extreme-weather
  features where the underlying temporal resolution supports them.
- Leakage-safe operational seasonal-weather features with expanding climate
  normal fallback for unsupported forecast leads.
- Separate archived operational and retrospective hindcast evaluation tracks.

### Forecasting research

- Seasonal naive, drift, Holt-Winters, SARIMA/SARIMAX and LightGBM baselines.
- Monthly and daily Temporal Fusion Transformer experiments on AMD ROCm.
- Chronos-2 zero-shot and bounded fine-tuning experiments.
- Rolling-origin horizons 1-12 with regional, seasonal and extreme-period
  evaluation.
- Residual correction, growth/log-ratio targets, validation-weighted ensembles,
  structural-bias diagnostics and scale-aware calibration research.

### Deterministic production V1

- Frozen E14 energy and E24-B peak recipes.
- Independently loadable fitted bundle with 27 statistical model states, two
  LightGBM models, frozen E14 weights, preprocessing state and a pinned local
  Chronos-2 checkpoint.
- Strict artifact checksums, feature-schema validation and cutoff audits.
- Twelve-month forecasts for eight weather zones plus ERCOT system totals.
- Atomic publication, immutable history, idempotent forecast IDs, monitoring,
  rollback, failure recovery and a non-activating scheduler preview.
- Internal shadow intervals that are never emitted as public confidence claims.

## Architecture

```text
Official sources
    │
    ▼
Validated acquisition ──► raw cache + checksums + provenance
    │
    ▼
Canonical Parquet ──────► ERCOT demand │ EIA retail sales │ ERA5 weather
    │
    ▼
Leakage-safe features ──► historical demand + calendar + operational weather
    │
    ▼
Rolling evaluation ─────► statistical │ LightGBM │ TFT │ Chronos-2 │ hybrids
    │
    ▼
Frozen V1 bundle ───────► E14 energy + E24-B peak
    │
    ▼
Guarded production ─────► validate │ infer │ history │ monitor │ rollback
```

ERCOT grid demand and EIA retail electricity sales remain separate targets and
are never merged into one series.

## Repository layout

```text
config/                 Dataset, experiment and production configuration
scripts/acquisition/    Source discovery, HTTP and validation modules
scripts/forecasting/    Features, models, calibration and production helpers
scripts/weather/        ERA5 and seasonal-weather processing
tests/                  Focused acquisition, leakage and forecasting tests
docs/                   Phase execution guides and current project status
reports/                Validation, quality and experiment reports
data/                   Local raw/processed/runtime data; excluded from Git
models/production/      Local frozen fitted bundle; excluded from Git
```

Start with [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) for the latest
resume point and [`docs/V1_PRODUCTION.md`](docs/V1_PRODUCTION.md) for production
safety behavior.

## Environment

- Python 3.12.
- CPU models use Statsmodels and LightGBM.
- TFT and Chronos-2 use PyTorch through ROCm's CUDA-compatible Python API.
- Verified GPU: AMD Radeon AI PRO R9700.
- System ROCm, ComfyUI and reference environments are not modified.

Dependency files are phase-specific:

```text
requirements-phase1.txt
requirements-phase2.txt
requirements-phase2b.txt
requirements-phase3a.txt
requirements-phase3b.txt
requirements-chronos2.txt
constraints-phase2b-rocm.txt
```

The ROCm Torch build is protected by constraints. Do not install an unpinned
CPU or NVIDIA Torch wheel into the EnergyCast environments.

## Common commands

Use the isolated project interpreter:

```bash
cd /home/karthiks/src/EnergyCast-AI
```

Validate and inspect the acquisition manifest:

```bash
.venv/bin/python scripts/download_datasets.py --validate-manifest
.venv/bin/python scripts/download_datasets.py --discover
.venv/bin/python scripts/download_datasets.py \
  --dataset ercot_hourly_load --discover
```

Validate the frozen production bundle:

```bash
.venv/bin/python scripts/run_production_v1.py validate-bundle
.venv/bin/python scripts/validate_production_v1_bundle.py \
  --bundle models/production/energycast_v1_202610
```

Run a complete non-publishing production dry run:

```bash
.venv/bin/python scripts/run_production_v1.py run --dry-run
```

Preview the monthly schedule without installing it:

```bash
.venv/bin/python scripts/run_production_v1.py schedule-preview
```

Focused production tests do not require downloading data:

```bash
.venv/bin/python -m unittest tests.test_production_v1 -v
```

Additional phase-specific execution commands are documented in
[`docs/`](docs/).

## Frozen bundle and large data

The validated fitted bundle is approximately 481 MiB because it vendors the
pinned Chronos-2 checkpoint. GitHub's standard per-file limit is 100 MiB, so
`models/production/` is intentionally excluded from Git. Recreate the local
bundle after the validated datasets and pinned Chronos snapshot are available:

```bash
.venv/bin/python scripts/freeze_production_v1_bundle.py
```

The bundle manifest records package versions, random seeds, component-specific
training cutoffs and SHA-256 checksums. The validation summary is committed at
[`reports/production/v1/FROZEN_BUNDLE_REPORT.md`](reports/production/v1/FROZEN_BUNDLE_REPORT.md).

Raw weather archives and processed datasets are also excluded from Git. The
repository contains acquisition code, manifests, validation reports and exact
manual execution procedures rather than redistributing upstream datasets.

## Important limitations

- Weather-zone boundaries are an explicitly documented approximation; they are
  not presented as authoritative ERCOT GIS boundaries.
- Archived seasonal release timestamps are schedule-based availability evidence,
  not audited file-publication timestamps.
- The 2017-2020 seasonal archive gap is not filled with fabricated forecasts.
- Retrospective hindcasts are research evidence, not real-time operational data.
- No missing demand or weather observations are silently fabricated.
- Deterministic forecasts are deployment-ready, but the first live publication,
  actual rollback and unattended scheduler activation require explicit approval.

## Documentation

- [Current project status](docs/PROJECT_STATUS.md)
- [V1 final validation](docs/V1_FINAL_VALIDATION.md)
- [Calibration remediation](docs/V1_CALIBRATION_REMEDY.md)
- [Production operations](docs/V1_PRODUCTION.md)
- [Phase 5 seasonal/weather execution](docs/PHASE5C_EXECUTION.md)
- [Structural-bias and transformed-target work](docs/PHASE5D_EXECUTION.md)

## License and source terms

Original EnergyCast AI source code and documentation are licensed under the
[Apache License 2.0](LICENSE). This project license does **not** grant rights to
redistribute third-party datasets or model artifacts.

- [Third-party software and model notices](THIRD_PARTY_NOTICES.md)
- [Dataset licenses, source terms and attribution](DATA_LICENSES.md)
- [Apache NOTICE](NOTICE)

The local production bundle is excluded from Git and is not approved for public
download until its distribution includes the complete applicable upstream
license texts, notices, model card and pinned-revision provenance.

