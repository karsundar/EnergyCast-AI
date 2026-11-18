#!/usr/bin/env python3
"""Run resumable Phase 4B CPU experiments E8-E10."""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import re
import resource
import signal
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[variable] = "1"

import numpy as np
import pandas as pd

from forecasting.hybrid import (
    apply_residual_model, assert_residual_samples_leakage_safe, fit_residual_model,
    historical_oos_residuals, residual_features, sarima_forecast,
    select_residual_transform, validation_residual_calibration,
)
from forecasting.weather_oracle import (
    MONTHLY_WEATHER_FEATURES, holt_winters_forecast, prepare_monthly,
    sarimax_forecast,
)

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
INTEGRATED = PROCESSED / "weather" / "regional" / "integrated"
OUTPUT = PROCESSED / "phase4b"
PREDICTIONS = PROCESSED / "phase4a" / "predictions"
REPORTS = ROOT / "reports" / "phase4b"
TARGETS = ("total_energy_mwh", "peak_demand_mw")
WORKER_FRAME: pd.DataFrame | None = None
WORKER_CACHES: dict[tuple[str, str], pd.DataFrame] = {}
WORKER_TRANSFORMS: dict[tuple[str, str], str] = {}
WORKER_INTERVALS: dict[tuple[str, str], dict[int, tuple[float, float]]] = {}
WORKER_CONFIG: dict[str, Any] = {}
STOP = False


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiments", default="E8,E9,E10")
    parser.add_argument("--start-origin", default="2020-12-01")
    parser.add_argument("--end-origin", default="2025-04-01")
    parser.add_argument("--max-horizon", type=int, default=12)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--lightgbm-threads", type=int)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--rebuild-residual-cache", action="store_true")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--evaluate-only", action="store_true")
    parser.add_argument("--comparison-experiments", default="E0,E1,E2,E3,E4,E5,E6,E7,E8,E9,E10")
    return parser.parse_args()


def stop_handler(_signum: int, _frame: object) -> None:
    global STOP
    STOP = True


def baseline_values(frame: pd.DataFrame, geography: str, target: str, origin: pd.Timestamp,
                    experiment: str, horizon: int, config: dict[str, Any]) -> tuple[pd.DatetimeIndex, np.ndarray]:
    group = frame[frame["geography"] == geography].set_index("month").sort_index()
    history = group[target].loc[:origin].dropna()
    months = pd.date_range(origin + pd.DateOffset(months=1), periods=horizon, freq="MS")
    if experiment in {"E8", "E17"}:
        values = sarima_forecast(history, horizon, config["sarimax"])
    elif experiment == "E9":
        values = holt_winters_forecast(history, horizon)
    else:
        values = sarimax_forecast(
            history, group.loc[history.index, list(MONTHLY_WEATHER_FEATURES)],
            group.loc[months, list(MONTHLY_WEATHER_FEATURES)], config["sarimax"],
        )
    return months, values


def initialize_worker(frame: pd.DataFrame, caches: dict[tuple[str, str], pd.DataFrame],
                      transforms: dict[tuple[str, str], str],
                      intervals: dict[tuple[str, str], dict[int, tuple[float, float]]],
                      config: dict[str, Any],
                      blas_threads: int) -> None:
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[variable] = str(blas_threads)
    if mp.current_process().name != "MainProcess":
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    global WORKER_FRAME, WORKER_CACHES, WORKER_TRANSFORMS, WORKER_INTERVALS, WORKER_CONFIG
    WORKER_FRAME = frame
    WORKER_CACHES = caches
    WORKER_TRANSFORMS = transforms
    WORKER_INTERVALS = intervals
    WORKER_CONFIG = config


def forecast_task(payload: tuple[str, str, pd.Timestamp, int]) -> tuple[str, pd.DataFrame, float]:
    experiment, target, origin, horizon = payload
    if WORKER_FRAME is None:
        raise RuntimeError("worker was not initialized")
    frame = WORKER_FRAME
    config = WORKER_CONFIG
    residuals = WORKER_CACHES.get((experiment, target))
    transform = WORKER_TRANSFORMS.get((experiment, target))
    intervals = WORKER_INTERVALS[(experiment, target)]
    started = time.perf_counter()
    include_weather = experiment == "E10"
    residual_model = None
    if residuals is not None and transform is not None:
        available = residuals[residuals["target_month"] <= origin].dropna()
        residual_model = fit_residual_model(available, transform, config["lightgbm"])
    records = []
    for geography in sorted(frame["geography"].unique()):
        months, baseline = baseline_values(frame, geography, target, origin, experiment, horizon, config)
        values = baseline.copy()
        if residual_model is not None:
            query = pd.DataFrame([
                residual_features(frame, geography, target, origin, month, step, float(value), include_weather)
                for step, (month, value) in enumerate(zip(months, baseline, strict=True), 1)
            ])
            values = apply_residual_model(residual_model, query, baseline, transform)
        actuals = frame[frame["geography"] == geography].set_index("month")[target]
        for step, (month, value) in enumerate(zip(months, values, strict=True), 1):
            actual = float(actuals.get(month, np.nan))
            records.append({
                "experiment": experiment,
                "model": {"E8": "sarima", "E9": "holt_winters_lgbm_residual",
                          "E10": "sarimax_lgbm_residual_oracle",
                          "E17": "sarima_lgbm_residual"}[experiment],
                "frequency": "monthly", "forecast_path": "direct_monthly", "target_name": target,
                "origin": origin, "target_month": month, "horizon": step, "geography": geography,
                "p50": float(value), "actual": actual, "oracle_weather": experiment == "E10",
                "forecast_status": "complete" if np.isfinite(actual) else "incomplete_horizon",
                "residual_transform": transform,
                "p10": float(value + intervals[step][0]),
                "p90": float(value + intervals[step][1]),
            })
    usage = resource.getrusage(resource.RUSAGE_SELF)
    result = pd.DataFrame(records)
    result.attrs["worker_peak_rss_kib"] = int(usage.ru_maxrss)
    return f"{experiment}:{target}:{origin:%Y-%m}", result, time.perf_counter() - started


def residual_cache(frame: pd.DataFrame, target: str, experiment: str, config: dict[str, Any],
                   phase4b: dict[str, Any], smoke: bool, rebuild: bool
                   ) -> tuple[pd.DataFrame, str | None, dict[str, float], dict[int, tuple[float, float]]]:
    baseline = {"E8": "sarima", "E9": "holt_winters", "E10": "sarimax_oracle",
                "E17": "sarima"}[experiment]
    path = OUTPUT / f"residual_cache_{baseline}_{target}{'_smoke' if smoke else ''}.parquet"
    if path.exists() and not rebuild:
        print(f"reusing residual cache {path.name}", flush=True)
        samples = pd.read_parquet(path)
    else:
        print(f"building residual cache {path.name}", flush=True)
        origins = pd.date_range(phase4b["residual_calibration_start"], phase4b["residual_calibration_end"], freq="MS")
        if smoke:
            origins = origins[::6]
        samples = historical_oos_residuals(frame, target, baseline, origins, int(phase4b["residual_horizon"]), config)
        path.parent.mkdir(parents=True, exist_ok=True)
        samples.to_parquet(path, index=False, compression="zstd")
        print(f"saved residual cache {path.name} rows={len(samples)}", flush=True)
    assert_residual_samples_leakage_safe(samples)
    if experiment == "E8":
        transform, scores = None, {}
        validation = samples[
            samples["target_month"].between("2018-01-01", phase4b["transform_selection_end"])
        ]
        intervals = {
            int(h): (float(g["level_residual"].quantile(.1)), float(g["level_residual"].quantile(.9)))
            for h, g in validation.groupby("horizon", observed=True)
        }
    else:
        transform, scores = select_residual_transform(samples, config["lightgbm"], phase4b["transform_selection_end"])
        intervals = validation_residual_calibration(
            samples, config["lightgbm"], transform, phase4b["transform_selection_end"],
        )
        print(f"selected {experiment}:{target} transform={transform} validation_wape={scores}", flush=True)
    return samples, transform, scores, intervals


def evaluate(comparison_experiments: list[str]) -> None:
    pattern = re.compile(r"^(E(?:[0-9]|1[0-7]))_(total_energy_mwh|peak_demand_mw)_\d{6}\.parquet$")
    parts = [path for path in PREDICTIONS.glob("*.parquet") if pattern.match(path.name)]
    frames = [pd.read_parquet(path) for path in parts if path.name.split("_")[0] in comparison_experiments]
    if not frames:
        raise RuntimeError("no selected prediction partitions found")
    predictions = pd.concat(frames, ignore_index=True)
    present = set(predictions["experiment"].unique())
    missing = set(comparison_experiments) - present
    if missing:
        raise RuntimeError(f"comparison is incomplete; missing experiments: {sorted(missing)}")
    keys = ["target_name", "origin", "target_month", "horizon", "geography"]
    valid = predictions[(predictions["forecast_status"] == "complete") & predictions[["p50", "actual"]].notna().all(axis=1)].copy()
    common = valid.groupby(keys, observed=True)["experiment"].nunique()
    common_keys = common[common == len(comparison_experiments)].index
    valid = valid.set_index(keys).loc[common_keys].reset_index()
    runtime_by_experiment: dict[str, float] = {}
    execution_path = REPORTS / "phase4b_execution.json"
    if execution_path.exists():
        for task in json.loads(execution_path.read_text()).get("tasks", []):
            experiment = task["task"].split(":", 1)[0]
            runtime_by_experiment[experiment] = runtime_by_experiment.get(experiment, 0.0) + float(task["seconds"])
    records = []
    for (experiment, target), group in valid.groupby(["experiment", "target_name"], observed=True):
        error = group["p50"] - group["actual"]
        interval_rows = group[["p10", "p90", "actual"]].dropna() if {"p10", "p90"}.issubset(group) else pd.DataFrame()
        records.append({
            "experiment": experiment, "target_name": target, "observations": len(group),
            "wape": float(error.abs().sum() / group["actual"].abs().sum() * 100),
            "mae": float(error.abs().mean()), "rmse": float(np.sqrt(np.square(error).mean())),
            "forecast_bias": float(error.mean()),
            "p10_p90_coverage": (float(interval_rows["actual"].between(interval_rows["p10"], interval_rows["p90"]).mean())
                                  if not interval_rows.empty else None),
            "interval_observations": len(interval_rows),
            "oracle_weather": bool(group["oracle_weather"].all()),
            "recorded_task_runtime_seconds": runtime_by_experiment.get(str(experiment)),
        })
    metrics = pd.DataFrame(records)
    REPORTS.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(REPORTS / "phase4b_metrics_comparable.csv", index=False)
    valid.to_parquet(OUTPUT / "phase4b_predictions_comparable.parquet", index=False, compression="zstd")
    summary = {"status": "complete", "experiments": comparison_experiments,
               "comparable_predictions": len(valid), "metrics": records}
    (REPORTS / "phase4b_evaluation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)


def main() -> int:
    options = arguments()
    if options.evaluate_only:
        evaluate(options.comparison_experiments.split(","))
        return 0
    experiments = options.experiments.split(",")
    if set(experiments) - {"E8", "E9", "E10", "E17"}:
        raise ValueError("CPU runner supports only E8-E10 and E17")
    config = json.loads((ROOT / "config" / "phase4a.json").read_text())
    phase4b = json.loads((ROOT / "config" / "phase4b.json").read_text())
    lightgbm_threads = int(options.lightgbm_threads or phase4b["lightgbm_threads_per_worker"])
    config["lightgbm"]["n_jobs"] = lightgbm_threads
    workers = int(options.workers or phase4b["workers"])
    frame = prepare_monthly(
        pd.read_parquet(INTEGRATED / "weather_demand_monthly.parquet"),
        pd.read_parquet(PROCESSED / "ercot_monthly_targets.parquet"),
    )
    origins = pd.date_range(options.start_origin, options.end_origin, freq="MS")
    if options.smoke_test:
        frame = frame[frame["geography"] == "ERCOT_SYSTEM"]
        origins = origins[:1]
        options.max_horizon = min(options.max_horizon, 2)
        workers = min(workers, 2)
    caches: dict[tuple[str, str], pd.DataFrame] = {}
    transforms: dict[tuple[str, str], str] = {}
    intervals: dict[tuple[str, str], dict[int, tuple[float, float]]] = {}
    selections = []
    for experiment in experiments:
        for target in TARGETS:
            samples, transform, scores, calibrated = residual_cache(
                frame, target, experiment, config, phase4b, options.smoke_test,
                options.rebuild_residual_cache,
            )
            caches[(experiment, target)] = samples
            if transform is not None:
                transforms[(experiment, target)] = transform
            intervals[(experiment, target)] = calibrated
            selections.append({"experiment": experiment, "target": target, "selected": transform,
                               "validation_wape": scores, "interval_source": "2018-2020 development residuals"})
    tasks = []
    for origin in origins:
        for experiment in experiments:
            for target in TARGETS:
                tasks.append((experiment, target, origin, options.max_horizon))
    output = OUTPUT / "smoke" if options.smoke_test else PREDICTIONS
    output.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    report_root = REPORTS / "smoke" if options.smoke_test else REPORTS
    report_root.mkdir(parents=True, exist_ok=True)
    progress_path = report_root / "progress.json"
    completed = set()
    if options.resume:
        for path in output.glob("E*_*.parquet"):
            match = re.match(r"^(E(?:8|9|10|17))_(total_energy_mwh|peak_demand_mw)_(\d{6})\.parquet$", path.name)
            if match:
                experiment, target, stamp = match.groups()
                completed.add(f"{experiment}:{target}:{stamp[:4]}-{stamp[4:]}")
        if progress_path.exists():
            completed.update(json.loads(progress_path.read_text()).get("completed", []))
    tasks = [task for task in tasks if f"{task[0]}:{task[1]}:{task[2]:%Y-%m}" not in completed]
    print(f"scheduled={len(tasks)} completed={len(completed)} workers={workers} lightgbm_threads={lightgbm_threads}", flush=True)
    report_path = report_root / ("phase4b_smoke.json" if options.smoke_test else "phase4b_execution.json")
    timings = []
    if options.resume and report_path.exists():
        timings = json.loads(report_path.read_text()).get("tasks", [])
    completed_this_run = 0
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    initialize_worker(frame, caches, transforms, intervals, config, int(phase4b["blas_threads_per_worker"]))
    if workers == 1:
        results = map(forecast_task, tasks)
    else:
        context = mp.get_context("spawn")
        executor = ProcessPoolExecutor(
            max_workers=workers, mp_context=context, initializer=initialize_worker,
            initargs=(frame, caches, transforms, intervals, config, int(phase4b["blas_threads_per_worker"])),
        )
        futures = [executor.submit(forecast_task, task) for task in tasks]
        results = (future.result() for future in as_completed(futures))
    try:
        for key, predictions, duration in results:
            if STOP:
                break
            experiment, target, year_month = key.split(":")
            path = output / f"{experiment}_{target}_{year_month.replace('-', '')}.parquet"
            temporary = path.with_suffix(".parquet.tmp")
            peak_rss = int(predictions.attrs.get("worker_peak_rss_kib", 0))
            predictions.to_parquet(temporary, index=False, compression="zstd")
            temporary.replace(path)
            completed.add(key)
            timings.append({"task": key, "seconds": duration, "rows": len(predictions),
                            "worker_peak_rss_kib": peak_rss})
            completed_this_run += 1
            progress = {"completed": sorted(completed), "pending_tasks": len(tasks) - completed_this_run,
                        "workers": workers, "lightgbm_threads_per_worker": lightgbm_threads}
            progress_tmp = progress_path.with_suffix(".json.tmp")
            progress_tmp.write_text(json.dumps(progress, indent=2) + "\n")
            progress_tmp.replace(progress_path)
            print(f"completed {key} rows={len(predictions)} seconds={duration:.2f}", flush=True)
    finally:
        if workers != 1:
            executor.shutdown(wait=not STOP, cancel_futures=True)
    report = {"status": ("interrupted" if STOP else ("smoke_passed" if options.smoke_test else "complete")), "workers": workers,
              "lightgbm_threads_per_worker": lightgbm_threads,
              "residual_transform_selection": selections, "tasks": timings}
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    return 130 if STOP else 0


if __name__ == "__main__":
    raise SystemExit(main())
