#!/usr/bin/env python3
"""Run resumable Phase 4A CPU oracle-weather experiments E0-E5."""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import re
import resource
import signal
import time
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Bound native math libraries before importing NumPy, SciPy, statsmodels, or LightGBM.
for _thread_variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                         "BLIS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_thread_variable] = "1"

import numpy as np
import pandas as pd

from forecasting.weather_oracle import (
    TARGETS, add_daily_lags, aggregate_daily_predictions, assert_oracle_leakage_safe,
    monthly_direct_samples, monthly_predictions, prepare_daily, prepare_monthly,
    recursive_daily_predictions,
)

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
WEATHER = PROCESSED / "weather" / "regional" / "integrated"
OUTPUT = PROCESSED / "phase4a"
REPORTS = ROOT / "reports" / "phase4a"
STOP = False
WORKER_MONTHLY: pd.DataFrame | None = None
WORKER_DAILY: pd.DataFrame | None = None
WORKER_CONFIG: dict[str, Any] = {}
WORKER_MONTHLY_SAMPLES: dict[tuple[str, bool], pd.DataFrame] = {}
WORKER_DAILY_ENRICHED: dict[str, pd.DataFrame] = {}


@dataclass(frozen=True)
class BacktestTask:
    experiment: str
    target: str
    origin: pd.Timestamp
    max_horizon: int

    @property
    def key(self) -> str:
        return f"{self.experiment}:{self.target}:{self.origin:%Y-%m}"


def stop_handler(_signum: int, _frame: object) -> None:
    global STOP
    STOP = True


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiments", default="E0,E1,E2,E3,E4,E5")
    parser.add_argument("--start-origin", default="2020-12-01")
    parser.add_argument("--end-origin", default="2025-04-01")
    parser.add_argument("--max-horizon", type=int, default=12)
    parser.add_argument("--geography", action="append")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--evaluate-only", action="store_true")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--workers", type=int, default=None, help="parallel process count; default from config (8)")
    parser.add_argument("--lightgbm-threads", type=int, default=None, help="LightGBM threads per worker; default 2")
    parser.add_argument("--benchmark-workers", help="benchmark comma-separated worker counts, e.g. 1,4,8,16")
    return parser.parse_args()


def initialize_worker(
    blas_threads: int,
    monthly: pd.DataFrame | None = None,
    daily: pd.DataFrame | None = None,
    config: dict[str, Any] | None = None,
    monthly_samples: dict[tuple[str, bool], pd.DataFrame] | None = None,
    daily_enriched: dict[str, pd.DataFrame] | None = None,
) -> None:
    """Apply per-process native thread limits and keep signals owned by the parent."""
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                     "BLIS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[variable] = str(blas_threads)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    try:
        from threadpoolctl import threadpool_limits
        threadpool_limits(limits=blas_threads)
    except ImportError:
        pass
    if monthly is not None:
        global WORKER_MONTHLY, WORKER_DAILY, WORKER_CONFIG
        global WORKER_MONTHLY_SAMPLES, WORKER_DAILY_ENRICHED
        WORKER_MONTHLY = monthly
        WORKER_DAILY = daily
        WORKER_CONFIG = config or {}
        WORKER_MONTHLY_SAMPLES = monthly_samples or {}
        WORKER_DAILY_ENRICHED = daily_enriched or {}


def configure_worker_state(monthly: pd.DataFrame, daily: pd.DataFrame, config: dict[str, Any],
                           max_horizon: int) -> None:
    """Build immutable feature caches once; forked workers share them copy-on-write."""
    global WORKER_MONTHLY, WORKER_DAILY, WORKER_CONFIG
    global WORKER_MONTHLY_SAMPLES, WORKER_DAILY_ENRICHED
    WORKER_MONTHLY = monthly
    WORKER_DAILY = daily
    WORKER_CONFIG = config
    WORKER_MONTHLY_SAMPLES = {
        (target, weather): monthly_direct_samples(monthly, target, max_horizon, weather)
        for target in TARGETS for weather in (False, True)
    }
    for samples in WORKER_MONTHLY_SAMPLES.values():
        assert_oracle_leakage_safe(samples)
    WORKER_DAILY_ENRICHED = {target: add_daily_lags(daily, target) for target in TARGETS}


def execute_task(task: BacktestTask) -> tuple[pd.DataFrame, dict[str, Any]]:
    if WORKER_MONTHLY is None or WORKER_DAILY is None:
        raise RuntimeError("worker datasets were not initialized")
    started = time.perf_counter()
    if task.experiment in {"E0", "E1", "E2", "E3"}:
        samples = WORKER_MONTHLY_SAMPLES[(task.target, task.experiment == "E3")]
        frame = monthly_predictions(
            WORKER_MONTHLY, task.target, [task.origin], task.experiment,
            WORKER_CONFIG, task.max_horizon, samples=samples,
        )
    else:
        daily_result = recursive_daily_predictions(
            WORKER_DAILY, task.target, task.origin + pd.offsets.MonthEnd(0), task.experiment,
            WORKER_CONFIG, task.max_horizon, enriched=WORKER_DAILY_ENRICHED[task.target],
        )
        frame = aggregate_daily_predictions(daily_result)
    elapsed = time.perf_counter() - started
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return frame, {
        "key": task.key, "experiment": task.experiment, "target": task.target,
        "origin": f"{task.origin:%Y-%m-%d}", "duration_seconds": elapsed,
        "worker_pid": os.getpid(), "worker_peak_rss_kib": int(usage.ru_maxrss),
        "rows": len(frame),
    }


def canonical_predictions(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize task-completion-dependent row and column ordering."""
    ordered = frame.sort_values(
        ["experiment", "target_name", "origin", "target_month", "horizon", "geography"]
    ).reset_index(drop=True)
    return ordered.sort_index(axis="columns")


def prediction_digest(frame: pd.DataFrame) -> str:
    ordered = canonical_predictions(frame)
    hashes = pd.util.hash_pandas_object(ordered, index=False).to_numpy().tobytes()
    return hashlib.sha256(hashes).hexdigest()


def run_task_batch(tasks: list[BacktestTask], workers: int, blas_threads: int) -> list[tuple[pd.DataFrame, dict[str, Any]]]:
    if workers == 1:
        initialize_worker(blas_threads)
        return [execute_task(task) for task in tasks]
    # Never fork after LightGBM/OpenMP has initialized: that can deadlock idle
    # children. Spawn clean interpreters and pass immutable caches once per worker.
    context = mp.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=workers, mp_context=context, initializer=initialize_worker,
        initargs=(blas_threads, WORKER_MONTHLY, WORKER_DAILY, WORKER_CONFIG,
                  WORKER_MONTHLY_SAMPLES, WORKER_DAILY_ENRICHED),
    ) as executor:
        futures = [executor.submit(execute_task, task) for task in tasks]
        return [future.result() for future in as_completed(futures)]


def benchmark(monthly: pd.DataFrame, daily: pd.DataFrame, config: dict[str, Any],
              worker_counts: list[int], lightgbm_threads: int, blas_threads: int) -> None:
    bounded_monthly = monthly[monthly["geography"] == "ERCOT_SYSTEM"].copy()
    bounded_daily = daily[daily["geography"] == "ERCOT_SYSTEM"].copy()
    configure_worker_state(bounded_monthly, bounded_daily, config, max_horizon=1)
    tasks = [BacktestTask(experiment, target, pd.Timestamp("2020-12-01"), 1)
             for experiment in ("E0", "E1", "E2", "E3", "E4", "E5") for target in TARGETS]
    runs: list[dict[str, Any]] = []
    reference: pd.DataFrame | None = None
    consistency_diagnostics: list[dict[str, Any]] = []
    for workers in worker_counts:
        started = time.perf_counter()
        results = run_task_batch(tasks, workers, blas_threads)
        wall = time.perf_counter() - started
        combined = pd.concat([frame for frame, _ in results], ignore_index=True)
        combined = canonical_predictions(combined)
        if reference is None:
            reference = combined
            consistent = True
        else:
            try:
                pd.testing.assert_frame_equal(reference, combined, check_exact=True)
                consistent = True
            except AssertionError as error:
                consistent = False
                numeric_columns = [column for column in ("p50", "actual") if column in combined]
                maximum_deltas = {
                    column: float((reference[column] - combined[column]).abs().max())
                    for column in numeric_columns
                }
                differing_rows = int(
                    (reference.astype(str) != combined.astype(str)).any(axis=1).sum()
                )
                consistency_diagnostics.append({
                    "workers": workers, "differing_rows": differing_rows,
                    "maximum_absolute_deltas": maximum_deltas,
                    "assertion": str(error).splitlines()[0],
                })
        by_pid: dict[int, int] = {}
        for _, performance in results:
            pid = int(performance["worker_pid"])
            by_pid[pid] = max(by_pid.get(pid, 0), int(performance["worker_peak_rss_kib"]))
        runs.append({
            "workers": workers, "wall_seconds": wall, "tasks": len(tasks),
            "tasks_per_second": len(tasks) / wall, "prediction_sha256": prediction_digest(combined),
            "exactly_matches_sequential": consistent,
            "aggregate_worker_peak_rss_kib": sum(by_pid.values()),
            "max_worker_peak_rss_kib": max(by_pid.values()),
        })
        print(f"benchmark workers={workers} wall={wall:.3f}s consistent={consistent}", flush=True)
    baseline = runs[0]["wall_seconds"]
    for run in runs:
        run["speedup_vs_first"] = baseline / run["wall_seconds"]
    passed = all(run["exactly_matches_sequential"] for run in runs)
    report = {
        "status": "passed" if passed else "failed_consistency",
        "workload": "E0-E5, two targets, one system origin, horizon 1",
        "lightgbm_threads_per_worker": lightgbm_threads, "blas_threads_per_worker": blas_threads,
        "runs": runs, "consistency_diagnostics": consistency_diagnostics,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "multiprocessing_benchmark.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    if not passed:
        raise RuntimeError("parallel predictions differ from sequential execution; diagnostics were saved")


def read_inputs(config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    monthly_weather = pd.read_parquet(WEATHER / "weather_demand_monthly.parquet")
    targets = pd.read_parquet(PROCESSED / "ercot_monthly_targets.parquet")
    monthly = prepare_monthly(monthly_weather, targets)
    daily = prepare_daily(pd.read_parquet(WEATHER / "weather_demand_daily.parquet"), config["completeness_threshold_pct"])
    return monthly, daily


def metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    valid = predictions[(predictions["forecast_status"] == "complete") & predictions[["p50", "actual"]].notna().all(axis=1)].copy()
    actual_keys = ["target_name", "geography", "target_month"]
    canonical_actuals = (
        valid[valid["forecast_path"] == "direct_monthly"][actual_keys + ["actual"]]
        .drop_duplicates()
    )
    if canonical_actuals.duplicated(actual_keys).any():
        raise RuntimeError("canonical direct-monthly actual values disagree")
    actual_lookup = canonical_actuals.set_index(actual_keys)["actual"]
    row_keys = pd.MultiIndex.from_frame(valid[actual_keys])
    canonical_for_row = actual_lookup.reindex(row_keys).to_numpy()
    tolerance = np.maximum(1e-6, np.abs(canonical_for_row) * 1e-10)
    mismatch = np.abs(valid["actual"].to_numpy() - canonical_for_row) > tolerance
    if mismatch.any():
        raise RuntimeError(
            f"{int(mismatch.sum())} aggregated-daily actual values disagree with canonical monthly targets; "
            "regenerate E4/E5 partitions"
        )
    valid["actual"] = canonical_for_row
    prior_month_keys = pd.MultiIndex.from_arrays([
        valid["target_name"], valid["geography"], valid["target_month"] - pd.DateOffset(months=1)
    ])
    prior_year_keys = pd.MultiIndex.from_arrays([
        valid["target_name"], valid["geography"], valid["target_month"] - pd.DateOffset(months=12)
    ])
    valid["prior_actual"] = actual_lookup.reindex(prior_month_keys).to_numpy()
    valid["prior_year_actual"] = actual_lookup.reindex(prior_year_keys).to_numpy()
    prediction_keys = ["experiment", "target_name", "forecast_path", "origin", "geography", "horizon"]
    prediction_lookup = valid.set_index(prediction_keys)["p50"]
    prior_prediction_keys = pd.MultiIndex.from_arrays([
        valid["experiment"], valid["target_name"], valid["forecast_path"], valid["origin"],
        valid["geography"], valid["horizon"] - 1,
    ])
    valid["prior_prediction"] = prediction_lookup.reindex(prior_prediction_keys).to_numpy()
    valid.loc[valid["horizon"] == 1, "prior_prediction"] = valid.loc[valid["horizon"] == 1, "prior_actual"]
    valid["direction_correct"] = (
        np.sign(valid["p50"] - valid["prior_prediction"])
        == np.sign(valid["actual"] - valid["prior_actual"])
    ).where(valid[["prior_prediction", "prior_actual"]].notna().all(axis=1))
    valid["yoy_growth_absolute_error"] = (
        (valid["p50"] - valid["actual"]).abs() / valid["prior_year_actual"].abs().replace(0, np.nan)
    )
    records = []
    groupings = {
        "overall": ["experiment", "target_name", "forecast_path"],
        "geography": ["experiment", "target_name", "forecast_path", "geography"],
        "horizon": ["experiment", "target_name", "forecast_path", "horizon"],
        "season": ["experiment", "target_name", "forecast_path", "season"],
    }
    valid["season"] = valid["target_month"].dt.month.map({12:"winter",1:"winter",2:"winter",3:"spring",4:"spring",5:"spring",6:"summer",7:"summer",8:"summer",9:"fall",10:"fall",11:"fall"})
    for breakdown, columns in groupings.items():
        for keys, frame in valid.groupby(columns, observed=True):
            keys = keys if isinstance(keys, tuple) else (keys,)
            error = frame["p50"] - frame["actual"]
            absolute = error.abs()
            denominator = frame["actual"].abs()
            unique_actual = frame.drop_duplicates(["geography", "target_month"]).sort_values("target_month")
            naive_scale = unique_actual.groupby("geography", observed=True)["actual"].diff(12).abs().mean()
            records.append({
                "breakdown": breakdown, **dict(zip(columns, keys, strict=True)), "observations": len(frame),
                "mae": absolute.mean(), "rmse": np.sqrt(np.square(error).mean()),
                "mape": (absolute / denominator.replace(0, np.nan)).mean() * 100,
                "wape": absolute.sum() / denominator.sum() * 100, "mase": absolute.mean() / naive_scale,
                "forecast_bias": error.mean(), "directional_accuracy": frame["direction_correct"].mean(),
                "yoy_growth_error": frame["yoy_growth_absolute_error"].mean(),
            })
    return pd.DataFrame.from_records(records)


def evaluate(output_root: Path = OUTPUT, report_root: Path = REPORTS) -> None:
    parts = sorted((output_root / "predictions").glob("*.parquet"))
    if not parts:
        raise RuntimeError(
            f"no Phase 4A prediction partitions found under {output_root / 'predictions'}. "
            "Run E0-E5 first, or use --smoke-test --evaluate-only to evaluate bounded smoke artifacts."
        )
    predictions = pd.concat([pd.read_parquet(path) for path in parts], ignore_index=True)
    comparable = predictions[predictions["forecast_status"] == "complete"].copy()
    common = comparable.groupby(["target_name", "origin", "target_month", "horizon", "geography"])["experiment"].nunique()
    required = comparable["experiment"].nunique()
    common_keys = common[common == required].index
    comparable = comparable.set_index(["target_name", "origin", "target_month", "horizon", "geography"]).loc[common_keys].reset_index()
    result = metrics(comparable)
    report_root.mkdir(parents=True, exist_ok=True)
    result.to_csv(report_root / "phase4a_metrics_comparable.csv", index=False)
    comparable.to_parquet(output_root / "phase4a_predictions_comparable.parquet", index=False, compression="zstd")
    summary = {"status": "complete", "experiments": sorted(comparable["experiment"].unique()), "comparable_predictions": len(comparable)}
    (report_root / "phase4a_evaluation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)


def main() -> int:
    args = arguments()
    if args.evaluate_only:
        output_root = OUTPUT / "smoke" if args.smoke_test else OUTPUT
        report_root = REPORTS / "smoke" if args.smoke_test else REPORTS
        evaluate(output_root, report_root)
        return 0
    config = json.loads((ROOT / "config" / "phase4a.json").read_text())
    execution = config["execution"]
    workers = int(args.workers if args.workers is not None else execution["workers"])
    lightgbm_threads = int(
        args.lightgbm_threads if args.lightgbm_threads is not None
        else execution["lightgbm_threads_per_worker"]
    )
    blas_threads = int(execution["blas_threads_per_worker"])
    if workers < 1 or lightgbm_threads < 1 or blas_threads < 1:
        raise ValueError("worker and thread counts must be positive")
    config["lightgbm"]["n_jobs"] = lightgbm_threads
    monthly, daily = read_inputs(config)
    if args.geography:
        monthly = monthly[monthly["geography"].isin(args.geography)]
        daily = daily[daily["geography"].isin(args.geography)]
    experiments = args.experiments.split(",")
    unsupported = set(experiments) - {"E0", "E1", "E2", "E3", "E4", "E5"}
    if unsupported:
        raise ValueError(f"CPU runner supports E0-E5; use run_phase4a_tft.py for {sorted(unsupported)}")
    if args.benchmark_workers:
        counts = [int(value) for value in args.benchmark_workers.split(",")]
        if any(value < 1 for value in counts):
            raise ValueError("benchmark worker counts must be positive")
        benchmark(monthly, daily, config, counts, lightgbm_threads, blas_threads)
        return 0
    origins = pd.date_range(args.start_origin, args.end_origin, freq="MS")
    if args.smoke_test:
        origins = origins[:1]
        args.max_horizon = min(args.max_horizon, 2)
        monthly = monthly[monthly["geography"] == "ERCOT_SYSTEM"]
        daily = daily[daily["geography"] == "ERCOT_SYSTEM"]
    configure_worker_state(monthly, daily, config, args.max_horizon)
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    output_root = OUTPUT / "smoke" if args.smoke_test else OUTPUT
    report_root = REPORTS / "smoke" if args.smoke_test else REPORTS
    prediction_dir = output_root / "predictions"
    prediction_dir.mkdir(parents=True, exist_ok=True)
    progress_path = report_root / "progress.json"
    report_root.mkdir(parents=True, exist_ok=True)
    completed = set()
    disk_completed: set[str] = set()
    partition_pattern = re.compile(r"^(E[0-5])_(total_energy_mwh|peak_demand_mw)_(\d{6})\.parquet$")
    for path in prediction_dir.glob("*.parquet"):
        match = partition_pattern.match(path.name)
        if match:
            experiment, target, year_month = match.groups()
            disk_completed.add(f"{experiment}:{target}:{year_month[:4]}-{year_month[4:]}")
    if args.resume and progress_path.exists():
        completed = set(json.loads(progress_path.read_text()).get("completed", []))
    if args.resume:
        completed |= disk_completed
    else:
        selected_keys = {
            f"{experiment}:{target}:{origin:%Y-%m}"
            for origin in origins for experiment in experiments for target in TARGETS
        }
        completed = disk_completed - selected_keys
    tasks = [
        BacktestTask(experiment, target, origin, args.max_horizon)
        for origin in origins for experiment in experiments for target in TARGETS
        if f"{experiment}:{target}:{origin:%Y-%m}" not in completed
    ]
    started = time.perf_counter()
    task_metrics: list[dict[str, Any]] = []
    performance_path = report_root / "execution_performance.json"
    context = mp.get_context("spawn")
    executor = ProcessPoolExecutor(
        max_workers=workers, mp_context=context, initializer=initialize_worker,
        initargs=(blas_threads, WORKER_MONTHLY, WORKER_DAILY, WORKER_CONFIG,
                  WORKER_MONTHLY_SAMPLES, WORKER_DAILY_ENRICHED),
    )
    futures: dict[Future[tuple[pd.DataFrame, dict[str, Any]]], BacktestTask] = {
        executor.submit(execute_task, task): task for task in tasks
    }
    interrupted = False
    try:
        for future in as_completed(futures):
            task = futures[future]
            frame, performance = future.result()
            path = prediction_dir / f"{task.experiment}_{task.target}_{task.origin:%Y%m}.parquet"
            temporary = path.with_suffix(".parquet.tmp")
            frame.to_parquet(temporary, index=False, compression="zstd")
            temporary.replace(path)
            completed.add(task.key)
            task_metrics.append(performance)
            elapsed = time.perf_counter() - started
            progress = {
                "completed": sorted(completed), "elapsed_seconds": elapsed,
                "workers": workers, "lightgbm_threads_per_worker": lightgbm_threads,
                "pending_tasks": len(tasks) - len(task_metrics),
            }
            progress_temporary = progress_path.with_suffix(".json.tmp")
            progress_temporary.write_text(json.dumps(progress, indent=2) + "\n")
            progress_temporary.replace(progress_path)
            by_experiment: dict[str, dict[str, float]] = {}
            for item in task_metrics:
                aggregate = by_experiment.setdefault(item["experiment"], {"tasks": 0, "task_cpu_wall_seconds": 0.0, "peak_worker_rss_kib": 0})
                aggregate["tasks"] += 1
                aggregate["task_cpu_wall_seconds"] += item["duration_seconds"]
                aggregate["peak_worker_rss_kib"] = max(aggregate["peak_worker_rss_kib"], item["worker_peak_rss_kib"])
            performance_report = {
                "workers": workers, "lightgbm_threads_per_worker": lightgbm_threads,
                "blas_threads_per_worker": blas_threads, "wall_seconds": elapsed,
                "experiments": by_experiment, "tasks": task_metrics,
            }
            performance_temporary = performance_path.with_suffix(".json.tmp")
            performance_temporary.write_text(json.dumps(performance_report, indent=2) + "\n")
            performance_temporary.replace(performance_path)
            print(
                f"completed {task.key} rows={len(frame)} task={performance['duration_seconds']:.1f}s "
                f"wall={elapsed:.1f}s pending={progress['pending_tasks']}", flush=True,
            )
            if STOP:
                interrupted = True
                break
    finally:
        if interrupted or STOP:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)
        else:
            executor.shutdown(wait=True)
    if interrupted or STOP:
        print("graceful stop requested; completed partitions and progress are saved", flush=True)
        return 130
    evaluate(output_root, report_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
