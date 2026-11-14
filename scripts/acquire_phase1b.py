#!/usr/bin/env python3
"""Acquire only the explicitly approved Phase 1B sample artifacts."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from acquisition.handlers import Artifact, discover_dataset
from download_datasets import download_artifact, load_manifest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "datasets.json"
OUTPUT = ROOT / "data" / "raw"
LEDGER = ROOT / "reports" / "acquisition_phase1b.json"
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
ERCOT_YEARS = {2004, 2020, 2025, 2026}
EIA_FILES = {"HS861M 1990-2009.xlsx", "sales_revenue.xlsx"}
FORECAST_FILE = "2025-ERCOT-Monthly-Peak-Demand-and-Energy-Forecast.xlsx"


def select_artifacts(dataset: dict[str, Any], artifacts: list[Artifact]) -> list[Artifact]:
    dataset_id = dataset["id"]
    if dataset_id == "ercot_hourly_load":
        return [artifact for artifact in artifacts if any(str(year) in artifact.filename for year in ERCOT_YEARS)]
    if dataset_id == "eia_monthly_electricity_sales":
        return [artifact for artifact in artifacts if artifact.filename in EIA_FILES]
    if dataset_id == "global_geopolitical_risk":
        return [artifact for artifact in artifacts if artifact.filename == "data_gpr_export.xls"]
    if dataset_id == "ercot_official_long_term_forecast":
        return [artifact for artifact in artifacts if artifact.filename == FORECAST_FILE]
    if dataset.get("source_handler") in {"fred", "new_york_fed"}:
        return artifacts
    return []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=("all", "workbooks", "fred"), default="all")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    datasets = load_manifest(MANIFEST)
    approved = [
        dataset for dataset in datasets
        if dataset["id"] in {
            "ercot_hourly_load",
            "eia_monthly_electricity_sales",
            "global_geopolitical_risk",
            "ercot_official_long_term_forecast",
        } or dataset.get("source_handler") in {"fred", "new_york_fed"}
    ]
    if args.group == "workbooks":
        approved = [dataset for dataset in approved if dataset.get("source_handler") not in {"fred", "new_york_fed"}]
    elif args.group == "fred":
        approved = [dataset for dataset in approved if dataset.get("source_handler") in {"fred", "new_york_fed"}]
    records: list[dict[str, Any]] = []
    for dataset in approved:
        configured = dict(dataset)
        configured["max_bytes"] = MAX_ARTIFACT_BYTES
        try:
            selected = select_artifacts(configured, discover_dataset(configured, timeout=60))
            expected_count = 4 if dataset["id"] == "ercot_hourly_load" else 2 if dataset["id"] == "eia_monthly_electricity_sales" else 1
            if len(selected) != expected_count:
                raise ValueError(f"expected {expected_count} approved artifacts, found {len(selected)}")
            for artifact in selected:
                record = download_artifact(configured, artifact, OUTPUT, 45, False)
                records.append(record)
                print(f"{record['status']}: {record['id']} {record['path']}", flush=True)
        except (OSError, ValueError) as exc:
            records.append({"id": dataset["id"], "status": "failed", "error": str(exc)})
    ledger = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Phase 1B bounded samples",
        "max_artifact_bytes": MAX_ARTIFACT_BYTES,
        "datasets": records,
    }
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    for record in records:
        if record["status"] == "failed":
            print(f"failed: {record['id']} {record['error']}")
    print(f"ledger: {LEDGER}")
    return 1 if any(record["status"] == "failed" for record in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
