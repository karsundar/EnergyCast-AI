#!/usr/bin/env python3
"""Audit Phase 5B seasonal caches and normalized partitions without downloading."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_phase5b_seasonal_bulk import CONFIG_PATHS, PROCESSED_ROOT, RAW_ROOT, fingerprint, request_for, sha256, tasks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=tuple(CONFIG_PATHS), default="seas5")
    parser.add_argument("--plan", choices=("priority", "project_full", "provider_full"), default="priority")
    parser.add_argument("--allow-incomplete", action="store_true")
    options = parser.parse_args()
    config = json.loads(CONFIG_PATHS[options.provider].read_text())
    progress_path = ROOT / config["progress_path"]
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {"entries": {}}
    failures: list[dict[str, object]] = []
    validated = 0
    rows = 0
    bytes_by_type = {"raw": 0, "processed": 0}
    for kind, date in tasks(config, options.plan):
        key = f"{config['provider']}:{kind}:{date:%Y-%m}"
        entry = progress["entries"].get(key)
        raw = RAW_ROOT / config["provider"] / kind / f"{date:%Y-%m}.nc"
        processed = PROCESSED_ROOT / config["provider"] / kind / f"year={date.year}" / f"{date:%Y-%m}.parquet"
        if not entry:
            failures.append({"key": key, "reason": "pending"})
            continue
        expected = fingerprint(config["dataset"], request_for(config, date))
        if entry.get("fingerprint") != expected or not raw.is_file() or not processed.is_file():
            failures.append({"key": key, "reason": "identity_or_file_missing"})
            continue
        if sha256(raw) != entry.get("sha256"):
            failures.append({"key": key, "reason": "checksum_mismatch"})
            continue
        frame = pd.read_parquet(processed, columns=[
            "issue_date", "available_at", "target_month", "lead_month", "ensemble_member",
            "latitude", "longitude", "forecast_kind", "forecast_system",
        ])
        keys = ["issue_date", "lead_month", "ensemble_member", "latitude", "longitude"]
        if frame.empty or frame.duplicated(keys).any() or sorted(frame.lead_month.unique()) != [1, 2, 3, 4, 5, 6]:
            failures.append({"key": key, "reason": "partition_structure_invalid"})
            continue
        if kind == "hindcast" and frame.available_at.notna().any():
            failures.append({"key": key, "reason": "synthetic_hindcast_availability"})
            continue
        validated += 1
        rows += len(frame)
        bytes_by_type["raw"] += raw.stat().st_size
        bytes_by_type["processed"] += processed.stat().st_size
    report = {
        "status": "complete" if not failures else "incomplete",
        "provider": config["provider"], "plan": options.plan,
        "expected_requests": len(tasks(config, options.plan)), "validated_requests": validated,
        "rows": rows, "bytes_by_type": bytes_by_type,
        "pending_or_invalid": len(failures), "failure_preview": failures[:25],
    }
    output = ROOT / "reports" / f"phase5b_{options.provider}_{options.plan}_validation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if not failures or options.allow_incomplete else 1


if __name__ == "__main__":
    raise SystemExit(main())
