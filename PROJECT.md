# EnergyCast AI

EnergyCast AI is in Phase 1: reproducible data acquisition, ingestion, and validation. The active scope is the 18-source manifest in `config/datasets.json`. Model training is prohibited until every enabled dataset passes the Phase 1 validation gate.

## Acquisition commands

```bash
python scripts/download_datasets.py --validate-manifest
python scripts/download_datasets.py --discover
python scripts/download_datasets.py --dataset ercot_hourly_load --discover
python scripts/download_datasets.py --dataset fred_industrial_production --dry-run
```

Use `--dataset` for bounded downloads. Copernicus execution remains disabled until CDS credentials and request parameters are validated.

Phase 1B reports are written to `reports/data_inventory.json` and `reports/data_quality_preliminary.md`. The complete manifest must remain unexecuted while FRED and Copernicus are blocked.
