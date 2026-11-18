#!/usr/bin/env python3
"""Resumable annual ERA5 acquisition and monthly normalization.

This command deliberately downloads one provider/year subset at a time.  AWS
Earthmover stores ERA5 in annual time chunks, so a yearly request avoids
re-reading the same source chunks for twelve independent monthly requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import signal
import sys
import time
from calendar import monthrange
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from acquisition.era5_providers import (  # noqa: E402
    AwsEarthmoverProvider,
    CANONICAL_VARIABLES,
)
from weather.era5 import (  # noqa: E402
    aggregate_daily_grid,
    normalize_hourly,
    request_fingerprint,
)

AWS_DATASET_URL = "s3://earthmover-icechunk-era5/icechunkV2#single/temporal"
TEXAS_BOUNDS = {"north": 36.5, "west": -106.75, "south": 25.75, "east": -93.5}
DEFAULT_RAW = ROOT / "data/raw/era5_historical_weather/aws"
DEFAULT_PROCESSED = ROOT / "data/processed/weather"
DEFAULT_REPORTS = ROOT / "reports"
LOG = logging.getLogger("era5-annual")
STOP_REQUESTED = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path, block_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, path)


def configure_logging(log_path: Path, verbose: bool = False) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S%z"
    )
    handlers: list[logging.Handler] = [logging.FileHandler(log_path)]
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    handlers.append(console)
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        handlers=handlers,
        force=True,
    )


def install_signal_handlers() -> None:
    def request_stop(signum: int, _frame: Any) -> None:
        global STOP_REQUESTED
        STOP_REQUESTED = True
        LOG.warning("graceful shutdown requested by signal %s", signum)

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)


def ensure_free_space(path: Path, minimum_gb: float) -> float:
    path.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(path).free / (1024**3)
    if free_gb < minimum_gb:
        raise RuntimeError(
            f"insufficient disk space: {free_gb:.2f} GiB free; "
            f"{minimum_gb:.2f} GiB required"
        )
    return free_gb


def load_progress(path: Path, start_year: int, end_year: int, end_month: int) -> dict[str, Any]:
    if path.exists():
        progress = json.loads(path.read_text())
        progress.update(
            {
                "requested_start_year": start_year,
                "requested_end_year": end_year,
                "requested_end_month": end_month,
                "updated_at": utc_now(),
            }
        )
        return progress
    return {
        "schema_version": 1,
        "provider": "aws_earthmover",
        "dataset_url": AWS_DATASET_URL,
        "snapshot": None,
        "requested_start_year": start_year,
        "requested_end_year": end_year,
        "requested_end_month": end_month,
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "years": {},
        "failures": [],
    }


def save_progress(path: Path, progress: dict[str, Any]) -> None:
    progress["updated_at"] = utc_now()
    atomic_json(path, progress)


def annual_paths(raw_dir: Path, year: int) -> tuple[Path, Path]:
    data = raw_dir / f"era5_texas_{year}.nc"
    return data, data.with_suffix(".request.json")


def year_request(year: int, end_month: int = 12) -> dict[str, Any]:
    last_day = monthrange(year, end_month)[1]
    end = f"{year}-{end_month:02d}-{last_day:02d}T23:00:00"
    # A partial final year stays inside one provider time chunk. Include the
    # Texas-local month boundary so accumulated fields close correctly.
    if end_month < 12:
        next_month = pd.Timestamp(year=year, month=end_month + 1, day=1)
        end = str(
            next_month.tz_localize("America/Chicago").tz_convert("UTC").tz_localize(None)
        )
    return {
        "provider": "aws_earthmover",
        "dataset_url": AWS_DATASET_URL,
        "variables": list(CANONICAL_VARIABLES),
        "bounds": dict(TEXAS_BOUNDS),
        "start": f"{year}-01-01T00:00:00",
        "end": end,
        "grid": [0.25, 0.25],
    }


def validate_annual_file(
    path: Path,
    sidecar: Path,
    expected_request: dict[str, Any],
    expected_grid: tuple[int, int] = (44, 54),
) -> dict[str, Any]:
    if not path.is_file() or not sidecar.is_file():
        raise ValueError("annual cache or request sidecar is missing")
    metadata = json.loads(sidecar.read_text())
    expected_fingerprint = request_fingerprint(expected_request)
    if metadata.get("request_sha256") != expected_fingerprint:
        raise ValueError("annual cache request fingerprint mismatch")
    checksum = sha256_file(path)
    if metadata.get("sha256") != checksum:
        raise ValueError("annual cache checksum mismatch")
    with xr.open_dataset(path) as dataset:
        missing = sorted(set(CANONICAL_VARIABLES) - set(dataset.data_vars))
        if missing:
            raise ValueError(f"annual cache missing variables: {missing}")
        if (dataset.sizes.get("latitude"), dataset.sizes.get("longitude")) != expected_grid:
            raise ValueError(
                "unexpected annual grid: "
                f"{dataset.sizes.get('latitude')}x{dataset.sizes.get('longitude')}"
            )
        times = pd.DatetimeIndex(dataset.valid_time.values)
        if times.empty or times.has_duplicates or not times.is_monotonic_increasing:
            raise ValueError("annual timestamps are empty, duplicated, or unordered")
        expected = pd.date_range(expected_request["start"], expected_request["end"], freq="h")
        if not times.equals(expected):
            raise ValueError(
                f"annual timestamp coverage mismatch: actual {times.min()}..{times.max()} "
                f"({len(times)}), expected {expected.min()}..{expected.max()} ({len(expected)})"
            )
    return {
        "sha256": checksum,
        "size_bytes": path.stat().st_size,
        "timestamps": len(times),
        "start": times.min().isoformat(),
        "end": times.max().isoformat(),
    }


def write_annual_subset(
    dataset: xr.Dataset,
    destination: Path,
    request: dict[str, Any],
    max_retries: int,
    backoff_seconds: float,
) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    if temporary.exists():
        temporary.unlink()
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        started = time.monotonic()
        try:
            subset = dataset.sel(
                valid_time=slice(request["start"], request["end"]),
                latitude=slice(TEXAS_BOUNDS["north"], TEXAS_BOUNDS["south"]),
                longitude=slice(TEXAS_BOUNDS["west"] % 360, TEXAS_BOUNDS["east"] % 360),
            )[list(CANONICAL_VARIABLES)]
            subset = subset.load()
            if STOP_REQUESTED:
                raise InterruptedError("shutdown requested after provider read")
            encoding = {
                name: {
                    "zlib": True,
                    "complevel": 1,
                    "shuffle": True,
                    "chunksizes": (min(168, subset.sizes["valid_time"]), 44, 54),
                }
                for name in CANONICAL_VARIABLES
            }
            subset.to_netcdf(temporary, engine="h5netcdf", encoding=encoding)
            with xr.open_dataset(temporary) as check:
                check.load()
            os.replace(temporary, destination)
            elapsed = time.monotonic() - started
            size = destination.stat().st_size
            return {
                "attempts": attempt,
                "duration_seconds": elapsed,
                "size_bytes": size,
                "output_mib_per_second": size / 1024**2 / max(elapsed, 0.001),
            }
        except InterruptedError:
            temporary.unlink(missing_ok=True)
            raise
        except Exception as error:  # provider errors vary by transport
            last_error = error
            temporary.unlink(missing_ok=True)
            if attempt >= max_retries:
                break
            delay = min(backoff_seconds * (2 ** (attempt - 1)), 300.0)
            LOG.warning(
                "annual attempt %d/%d failed: %s; retrying in %.1fs",
                attempt,
                max_retries,
                error,
                delay,
            )
            time.sleep(delay)
    raise RuntimeError(f"annual acquisition failed after {max_retries} attempts: {last_error}")


def acquire_year(
    source: xr.Dataset,
    raw_dir: Path,
    year: int,
    end_month: int,
    max_retries: int,
    backoff_seconds: float,
    snapshot: str,
) -> tuple[dict[str, Any], bool]:
    request = year_request(year, end_month)
    data_path, sidecar_path = annual_paths(raw_dir, year)
    try:
        result = validate_annual_file(data_path, sidecar_path, request)
        result["cache_hit"] = True
        LOG.info("%d validated cache hit: %s", year, data_path)
        return result, True
    except ValueError as error:
        if data_path.exists() or sidecar_path.exists():
            LOG.warning("%d cache rejected: %s", year, error)

    source_times = pd.DatetimeIndex(source.valid_time.values)
    requested_end = pd.Timestamp(request["end"])
    if source_times.max() < requested_end:
        raise RuntimeError(
            f"provider snapshot ends {source_times.max()}; requested {requested_end}. "
            "Resume after the provider snapshot advances or use another validated provider."
        )
    transfer = write_annual_subset(
        source, data_path, request, max_retries=max_retries, backoff_seconds=backoff_seconds
    )
    checksum = sha256_file(data_path)
    atomic_json(
        sidecar_path,
        {
            **request,
            "snapshot": snapshot,
            "request_sha256": request_fingerprint(request),
            "sha256": checksum,
            "size_bytes": data_path.stat().st_size,
            "created_at": utc_now(),
        },
    )
    result = validate_annual_file(data_path, sidecar_path, request)
    result.update(transfer)
    result["cache_hit"] = False
    return result, False


def month_output_paths(processed_dir: Path, year: int, month: int) -> dict[str, Path]:
    token = f"{year}-{month:02d}"
    return {
        "hourly": processed_dir / "hourly_grid" / f"year={year}" / f"era5_hourly_{token}.parquet",
        "daily": processed_dir / "daily_grid" / f"year={year}" / f"era5_daily_{token}.parquet",
        "monthly": processed_dir / "monthly_grid" / f"year={year}" / f"era5_monthly_{token}.parquet",
        "report": processed_dir / "validation" / f"era5_{token}.json",
    }


def validated_month_cache(paths: dict[str, Path], source_sha: str) -> bool:
    if not paths["report"].is_file():
        return False
    report = json.loads(paths["report"].read_text())
    if report.get("status") != "validated" or report.get("source_sha256") != source_sha:
        return False
    for key in ("hourly", "daily", "monthly"):
        path = paths[key]
        if not path.is_file() or report.get("outputs", {}).get(key, {}).get("sha256") != sha256_file(path):
            return False
    return True


def _frame_with_provenance(
    frame: pd.DataFrame, source_name: str, source_snapshot: str
) -> pd.DataFrame:
    frame = frame.copy()
    frame["source_file"] = source_name
    frame["source_provider"] = "aws_earthmover"
    frame["source_snapshot"] = source_snapshot
    return frame


def physical_range_flags(hourly: xr.Dataset) -> dict[str, int]:
    return {
        "temperature_c_outside_minus90_60": int(
            (((hourly.temperature_c < -90) | (hourly.temperature_c > 60)).sum()).item()
        ),
        "relative_humidity_outside_0_100": int(
            (((hourly.relative_humidity_pct < 0) | (hourly.relative_humidity_pct > 100)).sum()).item()
        ),
        "wind_speed_above_100_mps": int((hourly.wind_speed_mps > 100).sum().item()),
        "negative_precipitation": int((hourly.precipitation_mm < -1e-6).sum().item()),
        "negative_solar_radiation": int((hourly.solar_radiation_j_m2 < -1e-3).sum().item()),
    }


def aggregate_monthly_frame(daily: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    monthly = daily.groupby(["latitude", "longitude"], as_index=False).agg(
        temperature_mean_c=("temperature_mean_c", "mean"),
        temperature_min_c=("temperature_min_c", "min"),
        temperature_max_c=("temperature_max_c", "max"),
        relative_humidity_mean_pct=("relative_humidity_mean_pct", "mean"),
        wind_speed_mean_mps=("wind_speed_mean_mps", "mean"),
        precipitation_total_mm=("precipitation_total_mm", "sum"),
        solar_energy_total_mj_m2=("solar_energy_total_mj_m2", "sum"),
        hdd_65f=("hdd_65f", "sum"),
        cdd_65f=("cdd_65f", "sum"),
        observation_count=("observation_count", "sum"),
        expected_observation_count=("expected_observation_count", "sum"),
    )
    monthly.insert(0, "year", year)
    monthly.insert(1, "month", month)
    monthly["completeness_pct"] = (
        monthly.observation_count / monthly.expected_observation_count * 100.0
    )
    monthly["boundary_complete"] = monthly.completeness_pct == 100.0
    return monthly


def normalize_month_from_annual(
    annual_path: Path,
    next_annual_path: Path | None,
    processed_dir: Path,
    year: int,
    month: int,
    expected_grid: tuple[int, int] = (44, 54),
) -> dict[str, Any]:
    paths = month_output_paths(processed_dir, year, month)
    source_sha = sha256_file(annual_path)
    source_metadata_path = annual_path.with_suffix(".request.json")
    source_snapshot = "fixture"
    if source_metadata_path.is_file():
        source_snapshot = json.loads(source_metadata_path.read_text()).get("snapshot", "unknown")
    if validated_month_cache(paths, source_sha):
        return {"status": "validated", "cache_hit": True, "source_sha256": source_sha}

    start = pd.Timestamp(year=year, month=month, day=1)
    next_start = start + pd.offsets.MonthBegin(1)
    end = next_start - pd.Timedelta(hours=1)
    local_start = start.tz_localize("America/Chicago")
    local_end = next_start.tz_localize("America/Chicago")
    utc_boundary_end = local_end.tz_convert("UTC").tz_localize(None)

    with xr.open_dataset(annual_path) as current:
        datasets = [current]
        next_handle: xr.Dataset | None = None
        if next_annual_path is not None and next_annual_path.is_file():
            next_handle = xr.open_dataset(next_annual_path)
            datasets.append(next_handle)
        try:
            combined = xr.concat(datasets, dim="valid_time") if len(datasets) > 1 else current
            combined = combined.sortby("valid_time")
            combined = combined.isel(valid_time=~combined.get_index("valid_time").duplicated())
            times = pd.DatetimeIndex(combined.valid_time.values)
            expected_hours = pd.date_range(start, end, freq="h")
            actual_hours = times[(times >= start) & (times <= end)]
            if not actual_hours.equals(expected_hours):
                raise ValueError(
                    f"incomplete UTC month {year}-{month:02d}: "
                    f"{len(actual_hours)}/{len(expected_hours)} hours"
                )
            if (combined.sizes.get("latitude"), combined.sizes.get("longitude")) != expected_grid:
                raise ValueError("monthly grid dimensions do not match the configured Texas grid")
            if times.max() < utc_boundary_end:
                raise ValueError(
                    f"local-day boundary unavailable through {utc_boundary_end}; "
                    "the following annual cache is required"
                )

            monthly_source = combined.sel(valid_time=slice(start, end))
            hourly_ds = normalize_hourly(monthly_source, monthly_source).load()
            flags = physical_range_flags(hourly_ds)
            hourly = hourly_ds.to_dataframe().reset_index()
            hourly["timestamp_utc"] = pd.to_datetime(hourly.pop("valid_time"), utc=True)
            hourly["timestamp_local"] = hourly["timestamp_utc"].dt.tz_convert(
                "America/Chicago"
            )
            hourly = _frame_with_provenance(hourly, annual_path.name, source_snapshot)

            boundary_source = combined.sel(
                valid_time=slice(
                    local_start.tz_convert("UTC").tz_localize(None), utc_boundary_end
                )
            )
            boundary_ds = normalize_hourly(boundary_source, boundary_source).load()
            daily = aggregate_daily_grid(boundary_ds)
            target_dates = pd.date_range(start, next_start - pd.Timedelta(days=1), freq="D").date
            daily = daily.to_dataframe().reset_index()
            daily["local_date"] = pd.to_datetime(daily["local_date"]).dt.date
            daily = daily[daily["local_date"].isin(target_dates)].reset_index(drop=True)
            monthly = aggregate_monthly_frame(daily, year, month)
            daily = _frame_with_provenance(daily, annual_path.name, source_snapshot)
            monthly = _frame_with_provenance(monthly, annual_path.name, source_snapshot)
        finally:
            if next_handle is not None:
                next_handle.close()

    for key, frame in (("hourly", hourly), ("daily", daily), ("monthly", monthly)):
        atomic_parquet(frame, paths[key])
    outputs = {
        key: {
            "path": str(paths[key]),
            "size_bytes": paths[key].stat().st_size,
            "sha256": sha256_file(paths[key]),
            "rows": len(frame),
        }
        for key, frame in (("hourly", hourly), ("daily", daily), ("monthly", monthly))
    }
    report = {
        "status": "validated",
        "year": year,
        "month": month,
        "created_at": utc_now(),
        "source_file": str(annual_path),
        "source_sha256": source_sha,
        "provider": "aws_earthmover",
            "snapshot": source_snapshot,
        "expected_hours": len(expected_hours),
        "actual_hours": len(actual_hours),
        "grid": {"latitude": expected_grid[0], "longitude": expected_grid[1]},
        "physical_range_flags": flags,
        "outputs": outputs,
    }
    atomic_json(paths["report"], report)
    return {**report, "cache_hit": False}


def validate_completed(
    raw_dir: Path,
    processed_dir: Path,
    start_year: int,
    end_year: int,
    end_month: int,
) -> list[str]:
    errors: list[str] = []
    for year in range(start_year, end_year + 1):
        final_month = end_month if year == end_year else 12
        data, sidecar = annual_paths(raw_dir, year)
        try:
            result = validate_annual_file(data, sidecar, year_request(year, final_month))
        except (ValueError, OSError) as error:
            errors.append(f"{year}: {error}")
            continue
        for month in range(1, final_month + 1):
            paths = month_output_paths(processed_dir, year, month)
            if not validated_month_cache(paths, result["sha256"]):
                errors.append(f"{year}-{month:02d}: normalized partition not validated")
    return errors


def active_pid(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        pid_file.unlink(missing_ok=True)
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2004)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--end-month", type=int, default=10)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED)
    parser.add_argument("--progress", type=Path, default=DEFAULT_REPORTS / "era5_aws_history_progress.json")
    parser.add_argument("--log", type=Path, default=DEFAULT_REPORTS / "logs/era5_aws_history.log")
    parser.add_argument("--pid-file", type=Path, default=DEFAULT_REPORTS / "era5_aws_history.pid")
    parser.add_argument("--min-free-gb", type=float, default=50.0)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--backoff-seconds", type=float, default=15.0)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.start_year > args.end_year or not 1 <= args.end_month <= 12:
        raise SystemExit("invalid year or end-month range")
    configure_logging(args.log, args.verbose)
    if args.validate_only:
        errors = validate_completed(
            args.raw_dir, args.processed_dir, args.start_year, args.end_year, args.end_month
        )
        if errors:
            for error in errors:
                LOG.error(error)
            LOG.error("validation failed with %d issue(s)", len(errors))
            return 1
        LOG.info("all requested annual caches and monthly partitions validated")
        return 0

    existing_pid = active_pid(args.pid_file)
    if existing_pid is not None:
        LOG.error("another acquisition process is active with PID %d", existing_pid)
        return 2
    args.pid_file.parent.mkdir(parents=True, exist_ok=True)
    args.pid_file.write_text(f"{os.getpid()}\n")
    install_signal_handlers()
    progress = load_progress(args.progress, args.start_year, args.end_year, args.end_month)
    run_started = time.monotonic()
    completed_this_run = 0
    try:
        free_gb = ensure_free_space(args.raw_dir, args.min_free_gb)
        LOG.info("disk check passed: %.2f GiB free", free_gb)
        provider = AwsEarthmoverProvider()
        provenance = provider.provenance()
        snapshot = str(provenance["snapshot"])
        progress["snapshot"] = snapshot
        LOG.info("opening AWS Earthmover snapshot %s", snapshot)
        source = provider.open()
        try:
            source_max = pd.DatetimeIndex(source.valid_time.values).max()
            LOG.info("provider coverage ends %s", source_max)
            for year in range(args.start_year, args.end_year + 1):
                if STOP_REQUESTED:
                    break
                final_month = args.end_month if year == args.end_year else 12
                ensure_free_space(args.raw_dir, args.min_free_gb)
                year_state = progress["years"].setdefault(str(year), {})
                year_state.update({"status": "acquiring", "started_at": utc_now()})
                save_progress(args.progress, progress)
                try:
                    acquisition, cache_hit = acquire_year(
                        source,
                        args.raw_dir,
                        year,
                        final_month,
                        args.max_retries,
                        args.backoff_seconds,
                        snapshot,
                    )
                    year_state.update({"status": "normalizing", "acquisition": acquisition})
                    save_progress(args.progress, progress)
                    data_path, _ = annual_paths(args.raw_dir, year)
                    next_path, _ = annual_paths(args.raw_dir, year + 1)
                    months = year_state.setdefault("months", {})
                    normalization_end = final_month
                    if final_month == 12 and not next_path.exists():
                        normalization_end = 11
                        months["12"] = {
                            "status": "pending_next_year_boundary",
                            "updated_at": utc_now(),
                        }
                    for month in range(1, normalization_end + 1):
                        if STOP_REQUESTED:
                            break
                        result = normalize_month_from_annual(
                            data_path,
                            next_path if next_path.exists() else None,
                            args.processed_dir,
                            year,
                            month,
                        )
                        months[f"{month:02d}"] = result
                        save_progress(args.progress, progress)
                        LOG.info(
                            "%d-%02d normalized%s",
                            year,
                            month,
                            " (cache hit)" if result.get("cache_hit") else "",
                        )
                    if year > args.start_year:
                        prior_path, _ = annual_paths(args.raw_dir, year - 1)
                        prior_state = progress["years"].get(str(year - 1), {})
                        prior_months = prior_state.setdefault("months", {})
                        if prior_path.exists():
                            result = normalize_month_from_annual(
                                prior_path, data_path, args.processed_dir, year - 1, 12
                            )
                            prior_months["12"] = result
                            prior_state.update(
                                {"status": "validated", "completed_at": utc_now()}
                            )
                    if not STOP_REQUESTED:
                        status = (
                            "awaiting_next_year_boundary"
                            if final_month == 12 and not next_path.exists()
                            else "validated"
                        )
                        year_state.update({"status": status, "completed_at": utc_now()})
                        completed_this_run += 1
                    else:
                        year_state["status"] = "interrupted"
                    elapsed = time.monotonic() - run_started
                    total_years = args.end_year - args.start_year + 1
                    remaining = max(total_years - completed_this_run, 0)
                    eta = elapsed / max(completed_this_run, 1) * remaining
                    LOG.info(
                        "%d complete; elapsed %.1f min; estimated remaining %.1f min; cache=%s",
                        year,
                        elapsed / 60,
                        eta / 60,
                        cache_hit,
                    )
                    save_progress(args.progress, progress)
                except Exception as error:
                    year_state.update({"status": "failed", "error": str(error), "failed_at": utc_now()})
                    progress["failures"].append({"year": year, "at": utc_now(), "error": str(error)})
                    save_progress(args.progress, progress)
                    LOG.exception("year %d failed", year)
                    return 1
        finally:
            source.close()
        if STOP_REQUESTED:
            LOG.warning("stopped gracefully; rerun the same command to resume")
            return 130
        LOG.info("requested acquisition range completed")
        return 0
    finally:
        save_progress(args.progress, progress)
        args.pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
