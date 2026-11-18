#!/usr/bin/env python3
"""Acquire and normalize ERA5 history sequentially with durable monthly progress."""

from __future__ import annotations

import argparse
import calendar
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MIN_FREE_BYTES = 50 * 1024**3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2004-01")
    parser.add_argument("--end", default="2026-10")
    parser.add_argument("--max-months", type=int, help="bounded number of pending months to process")
    parser.add_argument("--progress", type=Path, default=Path("reports/era5_history_progress.json"))
    return parser.parse_args()


def months(start: str, end: str) -> list[str]:
    start_year, start_month = map(int, start.split("-"))
    end_year, end_month = map(int, end.split("-"))
    result = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        result.append(f"{year}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def previous_month(value: str) -> str | None:
    year, month = map(int, value.split("-"))
    if year == 2004 and month == 1:
        return None
    year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return f"{year}-{month:02d}"


def load_progress(path: Path, requested: list[str]) -> dict[str, Any]:
    if path.exists():
        progress = json.loads(path.read_text(encoding="utf-8"))
    else:
        progress = {"created_at": datetime.now(timezone.utc).isoformat(), "months": {}}
    progress["requested_start"] = requested[0]
    progress["requested_end"] = requested[-1]
    for value in requested:
        progress["months"].setdefault(value, {"status": "pending"})
    return progress


def save_progress(path: Path, progress: dict[str, Any]) -> None:
    progress["updated_at"] = datetime.now(timezone.utc).isoformat()
    statuses = [item["status"] for item in progress["months"].values()]
    progress["summary"] = {
        "validated": statuses.count("validated"), "pending": statuses.count("pending"),
        "failed": statuses.count("failed"), "processing": statuses.count("processing"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_text(json.dumps(progress, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def normalize(value: str) -> subprocess.CompletedProcess[str]:
    year, month = value.split("-")
    return run([sys.executable, "scripts/normalize_era5_month.py", "--year", year, "--month", month])


def main() -> int:
    args = parse_args()
    requested = months(args.start, args.end)
    progress = load_progress(args.progress, requested)
    pending = [value for value in requested if progress["months"][value]["status"] != "validated"]
    if args.max_months is not None:
        pending = pending[: args.max_months]
    for value in pending:
        free_before = shutil.disk_usage(Path.cwd()).free
        if free_before < MIN_FREE_BYTES:
            progress["months"][value] = {"status": "failed", "error": "less than 50 GiB free", "free_bytes": free_before}
            save_progress(args.progress, progress)
            return 2
        started = time.monotonic()
        progress["months"][value] = {"status": "processing", "started_at": datetime.now(timezone.utc).isoformat()}
        save_progress(args.progress, progress)
        ledger = Path("reports/era5_months") / f"{value.replace('-', '_')}_acquisition.json"
        acquisition = run([
            sys.executable, "scripts/download_datasets.py", "--dataset", "era5_historical_weather",
            "--cds-bulk", "--start-month", value, "--end-month", value, "--ledger", str(ledger),
        ])
        if acquisition.returncode:
            progress["months"][value] = {
                "status": "failed", "stage": "acquisition", "returncode": acquisition.returncode,
                "error": acquisition.stderr[-4000:], "duration_seconds": time.monotonic() - started,
            }
            save_progress(args.progress, progress)
            return acquisition.returncode
        normalized = normalize(value)
        if normalized.returncode:
            progress["months"][value] = {
                "status": "failed", "stage": "normalization", "returncode": normalized.returncode,
                "error": normalized.stderr[-4000:], "duration_seconds": time.monotonic() - started,
            }
            save_progress(args.progress, progress)
            return normalized.returncode
        report_path = Path("reports/era5_months") / f"{value.replace('-', '_')}.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        acquisition_ledger = json.loads(ledger.read_text(encoding="utf-8"))
        artifact = acquisition_ledger["datasets"][0]
        progress["months"][value] = {
            "status": "validated", "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": time.monotonic() - started, "download_status": artifact["status"],
            "request_sha256": artifact.get("request_sha256"), "archive_sha256": artifact["sha256"],
            "archive_bytes": artifact["bytes"], "missing_timestamps": len(report["missing_timestamps"]),
            "physical_flags": report["physical_flags"], "peak_ram_mb": report["peak_ram_mb"],
            "storage_bytes": sum(item["bytes"] for item in report["outputs"].values()),
            "free_bytes_after": shutil.disk_usage(Path.cwd()).free,
        }
        prior = previous_month(value)
        if prior and progress["months"].get(prior, {}).get("status") == "validated":
            refreshed = normalize(prior)
            if refreshed.returncode:
                progress["months"][prior]["boundary_refresh_error"] = refreshed.stderr[-2000:]
                save_progress(args.progress, progress)
                return refreshed.returncode
            prior_report = json.loads((Path("reports/era5_months") / f"{prior.replace('-', '_')}.json").read_text())
            progress["months"][prior]["boundary_complete"] = prior_report["boundary_available"]
            progress["months"][prior]["monthly_min_completeness_pct"] = prior_report["monthly_min_completeness_pct"]
        save_progress(args.progress, progress)
        print(f"validated {value}: {artifact['status']}, {artifact['bytes']} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
