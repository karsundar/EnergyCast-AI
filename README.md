# EnergyCast AI

Local AI energy forecasting project. Work is currently restricted to Phase 1: reproducible data acquisition, ingestion, and validation.

See [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) for the exact resume point and [`docs/PHASE_1.md`](docs/PHASE_1.md) for the validation gate.

## Phase 1 acquisition

Validate and discover sources before any bounded download:

```bash
python scripts/download_datasets.py --validate-manifest
python scripts/download_datasets.py --discover
python scripts/download_datasets.py --dataset ercot_hourly_load --discover
python scripts/download_datasets.py --dataset fred_industrial_production --dry-run
```

Downloads are stored under `data/raw/<dataset-id>/`. Existing files are reused when their checksum is valid. Acquisition metadata is written to `reports/acquisition.json`.

Do not run the complete manifest yet. Copernicus datasets are disabled until local CDS authentication and request parameters are validated.

