#!/usr/bin/env python3
"""Acquire only the approved Phase 1C historical artifacts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from acquisition.handlers import Artifact, discover_dataset
from download_datasets import download_artifact, load_manifest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "datasets.json"
OUTPUT = ROOT / "data" / "raw"
LEDGER = ROOT / "reports" / "acquisition_phase1c.json"
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
EIA_FILES = {"HS861M 1990-2009.xlsx", "sales_revenue.xlsx"}
FORECAST_MARKER = "Monthly-Peak-Demand-and-Energy-Forecast"


def select_artifacts(dataset: dict[str, Any], artifacts: list[Artifact]) -> list[Artifact]:
    dataset_id = dataset["id"]
    if dataset_id == "ercot_hourly_load":
        return artifacts
    if dataset_id == "eia_monthly_electricity_sales":
        return [artifact for artifact in artifacts if artifact.filename in EIA_FILES]
    if dataset_id == "ercot_official_long_term_forecast":
        return [artifact for artifact in artifacts if FORECAST_MARKER.lower() in artifact.filename.lower()]
    return []


def main() -> int:
    manifest = {dataset["id"]: dataset for dataset in load_manifest(MANIFEST)}
    records: list[dict[str, Any]] = []
    for dataset_id in (
        "ercot_hourly_load",
        "eia_monthly_electricity_sales",
        "ercot_official_long_term_forecast",
    ):
        dataset = dict(manifest[dataset_id])
        dataset["max_bytes"] = MAX_ARTIFACT_BYTES
        try:
            discovered = discover_dataset(dataset, timeout=60)
            selected = select_artifacts(dataset, discovered)
            if not selected:
                raise ValueError("no approved Phase 1C artifacts discovered")
            for artifact in selected:
                record = download_artifact(dataset, artifact, OUTPUT, 90, False)
                records.append(record)
                print(f"{record['status']}: {record['id']} {record['path']}", flush=True)
        except (OSError, ValueError) as exc:
            records.append({"id": dataset_id, "status": "failed", "error": str(exc)})
    ledger = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Phase 1C approved historical artifacts",
        "max_artifact_bytes": MAX_ARTIFACT_BYTES,
        "datasets": records,
    }
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    failures = [record for record in records if record["status"] == "failed"]
    for record in failures:
        print(f"failed: {record['id']} {record['error']}")
    print(f"ledger: {LEDGER}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
