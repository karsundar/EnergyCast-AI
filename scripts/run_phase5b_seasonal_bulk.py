#!/usr/bin/env python3
"""Sequential, resumable CDS seasonal acquisition for manual execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import time
from typing import Any

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.weather.seasonal import normalize_seas5_sample, validate_seasonal_netcdf

CONFIG_PATHS = {
    "seas5": ROOT / "config" / "phase5b_seasonal_bulk.json",
    "ncep": ROOT / "config" / "phase5b_ncep_bulk.json",
}
RAW_ROOT = ROOT / "data" / "raw" / "weather" / "seasonal" / "bulk"
PROCESSED_ROOT = ROOT / "data" / "processed" / "weather" / "seasonal" / "bulk"
STOP_REQUESTED = False


def stop_handler(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True
    logging.warning("shutdown requested; stopping after the active request")


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def months(start: str, end: str) -> list[pd.Timestamp]:
    return list(pd.date_range(start, end, freq="MS"))


def tasks(config: dict[str, Any], plan: str) -> list[tuple[str, pd.Timestamp]]:
    selected = config["plans"][plan]
    return ([('hindcast', date) for date in months(selected["hindcast_start"], selected["hindcast_end"])]
            + [('forecast', date) for date in months(selected["forecast_start"], selected["forecast_end"])])


def request_for(config: dict[str, Any], date: pd.Timestamp) -> dict[str, object]:
    return {
        "originating_centre": config["originating_centre"], "system": config["system"],
        "variable": config["variables"], "product_type": ["monthly_mean"],
        "year": f"{date.year:04d}", "month": f"{date.month:02d}",
        "leadtime_month": config["leadtime_month"], "area": config["texas_area_nwse"],
        "data_format": "netcdf",
    }


def fingerprint(dataset: str, request: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps({"dataset": dataset, "request": request}, sort_keys=True).encode()).hexdigest()


def estimate(config: dict[str, Any], plan: str) -> dict[str, object]:
    work = tasks(config, plan)
    hindcasts = sum(kind == "hindcast" for kind, _ in work)
    forecasts = len(work) - hindcasts
    cells = 11 * 14
    measured = config["measured_two_lead_sample"]
    normalized_rows = (hindcasts * int(measured["hindcast_members"]) * 6 * cells
                       + forecasts * int(measured["forecast_members"]) * 6 * cells)
    measured_raw = (hindcasts * int(measured["hindcast_raw_bytes"]) * 3
                    + forecasts * int(measured["forecast_raw_bytes"]) * 3)
    measured_processed = (hindcasts * int(measured["hindcast_processed_bytes"]) * 3
                          + forecasts * int(measured["forecast_processed_bytes"]) * 3)
    return {
        "plan": plan, "requests": len(work), "hindcast_requests": hindcasts,
        "forecast_requests": forecasts, "expected_normalized_rows": normalized_rows,
        "estimated_raw_bytes": measured_raw, "estimated_processed_bytes": measured_processed,
        "estimated_runtime_hours": [round(len(work) * 45 / 3600, 1), round(len(work) * 180 / 3600, 1)],
        "concurrency": 1, "bulk_authorized": False,
    }


def create_cds_client(config: dict[str, Any]) -> Any:
    """Keep library retries bounded so the runner's retry policy stays in control."""
    import cdsapi
    return cdsapi.Client(
        retry_max=1,
        sleep_max=int(config["retry_base_seconds"]),
    )


def is_retryable_exception(exc: Exception) -> bool:
    """Classify only transient HTTP/network failures for bounded outer retries."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in {429, 500, 502, 503, 504} or isinstance(exc, (
        requests.exceptions.ChunkedEncodingError,
        requests.exceptions.ConnectionError,
        requests.exceptions.ContentDecodingError,
        requests.exceptions.Timeout,
    ))


def completed(entry: dict[str, Any] | None, raw: Path, processed: Path, expected_fingerprint: str) -> bool:
    return bool(entry and entry.get("status") == "validated" and entry.get("fingerprint") == expected_fingerprint
                and raw.is_file() and processed.is_file() and sha256(raw) == entry.get("sha256"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=tuple(CONFIG_PATHS), default="seas5")
    parser.add_argument("--plan", choices=("priority", "project_full", "provider_full"), default="priority")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--acknowledge-cds-policy", action="store_true")
    parser.add_argument("--max-requests", type=int, help="bounded validation or partial manual run")
    options = parser.parse_args()
    config = json.loads(CONFIG_PATHS[options.provider].read_text())
    progress_path = ROOT / config["progress_path"]
    summary = estimate(config, options.plan)
    print(json.dumps(summary, indent=2), flush=True)
    if not options.execute:
        return 0
    if not options.acknowledge_cds_policy:
        raise RuntimeError("execution requires --acknowledge-cds-policy")
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {"entries": {}, "failures": []}
    work = tasks(config, options.plan)
    if options.max_requests is not None:
        work = work[:options.max_requests]
    client = None
    for index, (kind, date) in enumerate(work, start=1):
        if STOP_REQUESTED:
            break
        key = f"{config['provider']}:{kind}:{date:%Y-%m}"
        request = request_for(config, date)
        identity = fingerprint(config["dataset"], request)
        raw = RAW_ROOT / config["provider"] / kind / f"{date:%Y-%m}.nc"
        processed = PROCESSED_ROOT / config["provider"] / kind / f"year={date.year}" / f"{date:%Y-%m}.parquet"
        if completed(progress["entries"].get(key), raw, processed, identity):
            logging.info("cache hit %s", key)
            continue
        free = shutil.disk_usage(ROOT).free
        if free < int(config["minimum_free_gib"]) * 1024 ** 3:
            raise RuntimeError(f"disk guard: only {free / 1024**3:.1f} GiB free")
        raw.parent.mkdir(parents=True, exist_ok=True)
        processed.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        error: Exception | None = None
        frame: pd.DataFrame | None = None
        # Recover an already validated raw response if shutdown occurred before
        # normalization/progress persistence. This avoids a duplicate CDS job.
        if raw.is_file():
            try:
                validate_seasonal_netcdf(raw, {"t2m", "d2m", "u10", "v10", "tprate", "msdsrf"})
                frame = normalize_seas5_sample(
                    raw, "retrospective_hindcast" if kind == "hindcast" else "archived_realtime_forecast",
                    config["provider"],
                )
                with tempfile.NamedTemporaryFile(dir=processed.parent, suffix=".parquet", delete=False) as handle:
                    recovered = Path(handle.name)
                frame.to_parquet(recovered, index=False, compression="zstd")
                os.replace(recovered, processed)
                logging.info("recovered cached raw response %s", key)
            except Exception as exc:
                logging.warning("cached raw response was not reusable for %s: %s", key, exc)
                frame = None
        for attempt in range(1, int(config["retry_attempts"]) + 1) if frame is None else ():
            try:
                with tempfile.TemporaryDirectory(dir=raw.parent) as temporary_dir:
                    temporary = Path(temporary_dir) / "response.nc"
                    if client is None:
                        client = create_cds_client(config)
                    client.retrieve(config["dataset"], request, str(temporary))
                    validate_seasonal_netcdf(temporary, {"t2m", "d2m", "u10", "v10", "tprate", "msdsrf"})
                    frame = normalize_seas5_sample(
                        temporary, "retrospective_hindcast" if kind == "hindcast" else "archived_realtime_forecast",
                        config["provider"],
                    )
                    temporary_parquet = Path(temporary_dir) / "normalized.parquet"
                    frame.to_parquet(temporary_parquet, index=False, compression="zstd")
                    os.replace(temporary, raw)
                    os.replace(temporary_parquet, processed)
                error = None
                break
            except Exception as exc:
                error = exc
                if not is_retryable_exception(exc) or attempt == int(config["retry_attempts"]):
                    break
                delay = int(config["retry_base_seconds"]) * 2 ** (attempt - 1)
                logging.warning("transient failure %s attempt %d; retrying in %ds: %s", key, attempt, delay, exc)
                time.sleep(delay)
        if error is not None:
            progress["failures"].append({"key": key, "error": str(error), "time": pd.Timestamp.now(tz="UTC").isoformat()})
            atomic_json(progress_path, progress)
            raise error
        if frame is None:
            raise RuntimeError(f"no normalized frame produced for {key}")
        entry = {"status": "validated", "fingerprint": identity, "request": request,
                 "raw_path": str(raw), "processed_path": str(processed), "bytes": raw.stat().st_size,
                 "processed_bytes": processed.stat().st_size, "sha256": sha256(raw),
                 "rows": len(frame), "seconds": time.monotonic() - started,
                 "completed_at": pd.Timestamp.now(tz="UTC").isoformat()}
        progress["entries"][key] = entry
        progress["updated_at"] = entry["completed_at"]
        atomic_json(progress_path, progress)
        elapsed = sum(item.get("seconds", 0) for item in progress["entries"].values())
        rate = len(progress["entries"]) / elapsed if elapsed else 0
        remaining = max(0, len(tasks(config, options.plan)) - len(progress["entries"]))
        logging.info("completed %s (%d/%d) rows=%d speed=%.3f requests/s eta=%.1fh",
                     key, index, len(work), len(frame), rate, remaining / rate / 3600 if rate else 0)
        if index < len(work):
            time.sleep(int(config["inter_request_seconds"]))
    logging.info("stopped cleanly; rerun the same command to resume")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
