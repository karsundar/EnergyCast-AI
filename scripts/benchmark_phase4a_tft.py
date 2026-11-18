#!/usr/bin/env python3
"""Bounded, one-variable-at-a-time E7 TFT throughput benchmark."""

from __future__ import annotations

import argparse
import copy
import json
import math
import resource
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import lightning as L
import pandas as pd
import torch

from forecasting.weather_oracle import prepare_daily
from forecasting.weather_tft import assert_finite_frame, build_datasets, build_model, prepare_frame

ROOT = Path(__file__).resolve().parents[1]
INTEGRATED = ROOT / "data" / "processed" / "weather" / "regional" / "integrated"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=("total_energy_mwh", "peak_demand_mw"), default="total_energy_mwh")
    parser.add_argument("--warmup-batches", type=int, default=5)
    parser.add_argument("--measure-batches", type=int, default=20)
    parser.add_argument("--suite", choices=("quick", "full"), default="full")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "phase4a" / "tft_performance_benchmark.json")
    return parser.parse_args()


def benchmark_configurations(config: dict[str, Any], suite: str) -> list[dict[str, Any]]:
    reference = copy.deepcopy(config["tft"]["performance_reference"])
    reference["precision"] = config["tft"]["precision"]
    reference["name"] = "reference"
    configurations = [reference]
    batch_sizes = [32, 64, 128] if suite == "quick" else config["tft"]["performance_sweep"]["batch_sizes"]
    for value in batch_sizes:
        configurations.append({**reference, "name": f"batch_{value}", "batch_size_daily": value})
    workers = [4, 8] if suite == "quick" else [4, 8, 16]
    for value in workers:
        configurations.append({**reference, "name": f"workers_{value}", "num_workers": value})
    worker_base = {**reference, "num_workers": 4}
    configurations.extend([
        {**reference, "name": "pin_memory", "pin_memory": True},
        {**worker_base, "name": "persistent_workers", "persistent_workers": True},
        {**worker_base, "name": "prefetch_4", "prefetch_factor": 4},
        {**reference, "name": "bf16_mixed", "precision": "bf16-mixed"},
        {**reference, "name": "deterministic_true", "deterministic": "true"},
        {**reference, "name": "deterministic_false", "deterministic": "false"},
    ])
    return configurations


def move_to_device(value: Any, device: torch.device, non_blocking: bool) -> Any:
    if isinstance(value, torch.Tensor):
        return value.to(device, non_blocking=non_blocking)
    if isinstance(value, dict):
        return {key: move_to_device(item, device, non_blocking) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(move_to_device(item, device, non_blocking) for item in value)
    if isinstance(value, list):
        return [move_to_device(item, device, non_blocking) for item in value]
    return value


def gpu_use() -> float | None:
    executable = shutil.which("rocm-smi")
    if not executable:
        return None
    try:
        result = subprocess.run([executable, "--showuse", "--json"], capture_output=True, text=True, timeout=5, check=True)
        payload = json.loads(result.stdout)
        for card in payload.values():
            if isinstance(card, dict):
                for key, value in card.items():
                    if "GPU use" in key:
                        return float(str(value).replace("%", "").strip())
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    return None


class UtilizationMonitor:
    def __init__(self) -> None:
        self.samples: list[float] = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop_event.wait(1.0):
            value = gpu_use()
            if value is not None:
                self.samples.append(value)

    def __enter__(self) -> "UtilizationMonitor":
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop_event.set()
        self.thread.join(timeout=6)


def synchronize() -> None:
    torch.cuda.synchronize(0)


def timed(callable_: Any) -> tuple[Any, float]:
    synchronize()
    started = time.perf_counter()
    value = callable_()
    synchronize()
    return value, time.perf_counter() - started


def run_configuration(training: Any, tft: dict[str, Any], setting: dict[str, Any],
                      warmup_batches: int, measure_batches: int) -> dict[str, Any]:
    L.seed_everything(int(tft["seed"]), workers=True)
    deterministic = setting["deterministic"]
    torch.use_deterministic_algorithms(deterministic == "true", warn_only=deterministic == "warn")
    loader_args = {
        "num_workers": int(setting["num_workers"]), "pin_memory": bool(setting["pin_memory"]),
        "persistent_workers": bool(setting["persistent_workers"]),
    }
    if loader_args["num_workers"]:
        loader_args["prefetch_factor"] = int(setting["prefetch_factor"])
    loader = training.to_dataloader(
        train=True, batch_size=int(setting["batch_size_daily"]), **loader_args,
    )
    device = torch.device("cuda:0")
    model = build_model(training, tft).to(device).train()
    optimizer = torch.optim.Adam(model.parameters(), lr=float(tft["learning_rate"]))
    use_bf16 = setting.get("precision", "32-true") == "bf16-mixed"
    iterator = iter(loader)
    torch.cuda.reset_peak_memory_stats(0)
    phases = {name: 0.0 for name in ("data_loading", "host_to_device", "forward", "backward", "optimizer")}
    measured_samples = 0
    measured_steps = 0
    measured_wall_seconds = 0.0
    losses: list[float] = []
    total_started = time.perf_counter()
    with UtilizationMonitor() as utilization:
        for step in range(warmup_batches + measure_batches):
            step_started = time.perf_counter()
            loading_started = time.perf_counter()
            try:
                batch = next(iterator)
            except StopIteration:
                iterator = iter(loader)
                batch = next(iterator)
            loading_seconds = time.perf_counter() - loading_started
            batch, transfer_seconds = timed(lambda: move_to_device(batch, device, bool(setting["pin_memory"])))
            x, y = batch
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=use_bf16):
                output, forward_seconds = timed(lambda: model(x))
                loss = model.loss(output.prediction, y)
            _, backward_seconds = timed(loss.backward)
            _, optimizer_seconds = timed(optimizer.step)
            if step >= warmup_batches:
                synchronize()
                measured_wall_seconds += time.perf_counter() - step_started
                phases["data_loading"] += loading_seconds
                phases["host_to_device"] += transfer_seconds
                phases["forward"] += forward_seconds
                phases["backward"] += backward_seconds
                phases["optimizer"] += optimizer_seconds
                measured_steps += 1
                measured_samples += int(x["encoder_target"].shape[0])
                losses.append(float(loss.detach().cpu()))
    profiled_seconds = sum(phases.values())
    if not losses or not all(math.isfinite(value) for value in losses):
        raise RuntimeError("non-finite or missing benchmark loss")
    result = {
        **setting, "status": "passed", "warmup_batches": warmup_batches,
        "measured_batches": measured_steps, "measured_samples": measured_samples,
        "phase_seconds": phases, "profiled_seconds": profiled_seconds,
        "measured_wall_seconds": measured_wall_seconds,
        "wall_seconds_including_warmup": time.perf_counter() - total_started,
        "samples_per_second": measured_samples / measured_wall_seconds,
        "batches_per_second": measured_steps / measured_wall_seconds,
        "projected_epoch_seconds": len(loader) / (measured_steps / measured_wall_seconds),
        "epoch_batches": len(loader), "mean_loss": sum(losses) / len(losses),
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(0),
        "peak_reserved_bytes": torch.cuda.max_memory_reserved(0),
        "cpu_max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "gpu_utilization_samples": utilization.samples,
        "gpu_utilization_mean_pct": sum(utilization.samples) / len(utilization.samples) if utilization.samples else None,
        "gpu_utilization_max_pct": max(utilization.samples) if utilization.samples else None,
    }
    del model, optimizer, batch
    torch.cuda.empty_cache()
    return result


def main() -> int:
    options = arguments()
    if options.warmup_batches < 1 or options.measure_batches < 1:
        raise ValueError("warmup-batches and measure-batches must be positive")
    if not torch.cuda.is_available():
        raise RuntimeError("ROCm GPU unavailable; CPU fallback is forbidden")
    config = json.loads((ROOT / "config" / "phase4a.json").read_text())
    source = prepare_daily(pd.read_parquet(INTEGRATED / "weather_demand_daily.parquet"), config["completeness_threshold_pct"])
    source = source[source["target_valid"]]
    frame, categoricals, weather = prepare_frame(source, options.target, "daily")
    assert_finite_frame(frame, weather)
    training, _ = build_datasets(
        frame, categoricals, weather, int(config["tft"]["encoder_days"]), int(config["tft"]["decoder_days"]),
        config["splits"]["training_end"], config["splits"]["validation_end"],
    )
    results: list[dict[str, Any]] = []
    for setting in benchmark_configurations(config, options.suite):
        print(f"benchmarking {setting['name']} ...", flush=True)
        try:
            result = run_configuration(training, config["tft"], setting, options.warmup_batches, options.measure_batches)
        except torch.OutOfMemoryError as error:
            torch.cuda.empty_cache()
            result = {**setting, "status": "out_of_memory", "error": str(error)}
        except RuntimeError as error:
            result = {**setting, "status": "failed", "error": str(error)}
        results.append(result)
        print(json.dumps({key: result.get(key) for key in ("name", "status", "samples_per_second", "projected_epoch_seconds", "gpu_utilization_mean_pct", "peak_reserved_bytes")}), flush=True)
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(json.dumps({"status": "in_progress", "results": results}, indent=2) + "\n")
    passed = [result for result in results if result["status"] == "passed"]
    fastest = max(passed, key=lambda result: result["samples_per_second"]) if passed else None
    report = {
        "status": "complete" if passed else "failed", "experiment": "E7", "target": options.target,
        "method": "one_variable_at_a_time", "torch": torch.__version__, "hip": torch.version.hip,
        "gpu": torch.cuda.get_device_name(0), "fastest_configuration": fastest["name"] if fastest else None,
        "results": results,
    }
    options.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "fastest_configuration": report["fastest_configuration"], "output": str(options.output)}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
