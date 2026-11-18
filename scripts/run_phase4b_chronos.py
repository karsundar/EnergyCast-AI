#!/usr/bin/env python3
"""Manual E11-E13 Chronos-2 runner; never imports or downloads Chronos unless invoked."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from forecasting.weather_oracle import MONTHLY_WEATHER_FEATURES, prepare_monthly

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
INTEGRATED = PROCESSED / "weather" / "regional" / "integrated"
OUTPUT = PROCESSED / "phase4a" / "predictions"
REPORTS = ROOT / "reports" / "phase4b"
TARGETS = ("total_energy_mwh", "peak_demand_mw")


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", choices=("E11", "E12", "E13"), required=True)
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--start-origin", default="2020-12-01")
    parser.add_argument("--end-origin", default="2025-04-01")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--allow-fine-tune", action="store_true")
    parser.add_argument("--validation-run", action="store_true")
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--precision", choices=("fp32", "bf16"), default="fp32")
    parser.add_argument("--benchmark-batch-sizes", help="comma-separated bounded inference benchmark")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def load_pipeline(config: dict, experiment: str, smoke: bool, frame: pd.DataFrame,
                  target: str, precision: str, batch_size: int):
    try:
        from chronos import Chronos2Pipeline
    except ImportError as error:
        raise RuntimeError("Chronos-2 environment missing; follow docs/PHASE4B_EXECUTION.md") from error
    settings = config["chronos2"]
    import torch
    if not torch.cuda.is_available() or torch.version.hip is None:
        raise RuntimeError("Chronos-2 requires the verified ROCm GPU; CPU fallback is disabled")
    if precision == "bf16" and not torch.cuda.is_bf16_supported():
        raise RuntimeError("BF16 was requested but is not supported by this GPU/runtime")
    dtype = torch.bfloat16 if precision == "bf16" else torch.float32
    pipeline = Chronos2Pipeline.from_pretrained(
        settings["model_id"], revision=settings["revision"], device_map="cuda", dtype=dtype,
    )
    if experiment == "E13":
        training = []
        validation = []
        for _, group in frame.groupby("geography", observed=True):
            ordered = group.sort_values("month")
            training.append({"target": ordered.loc[ordered["month"] <= "2017-12-01", target].to_numpy(dtype=np.float32)})
            validation.append({"target": ordered.loc[ordered["month"] <= "2020-12-01", target].to_numpy(dtype=np.float32)})
        pipeline = pipeline.fit(
            inputs=training, validation_inputs=validation, prediction_length=12,
            finetune_mode=settings["fine_tune_mode"],
            num_steps=2 if smoke else int(settings["fine_tune_steps"]),
            learning_rate=float(settings["learning_rate"]), batch_size=batch_size,
            output_dir=str(ROOT / "models" / "phase4b" / "E13" / target),
            # Chronos detects the ROCm CUDA-compatible interface as an NVIDIA
            # SM80 device and otherwise enables TF32, which Transformers rejects
            # on AMD. ROCm BF16 is supported and is selected explicitly.
            tf32=False, bf16=precision == "bf16", fp16=False,
        )
    return pipeline


def main() -> int:
    options = arguments()
    if options.experiment == "E13" and not options.allow_fine_tune:
        raise RuntimeError("E13 requires explicit --allow-fine-tune")
    phase4b = json.loads((ROOT / "config" / "phase4b.json").read_text())
    frame = prepare_monthly(
        pd.read_parquet(INTEGRATED / "weather_demand_monthly.parquet"),
        pd.read_parquet(PROCESSED / "ercot_monthly_targets.parquet"),
    )
    if options.smoke_test:
        frame = frame[frame["geography"] == "ERCOT_SYSTEM"]
    batch_size = int(options.batch_size or phase4b["chronos2"]["batch_size"])
    pipeline = load_pipeline(phase4b, options.experiment, options.smoke_test, frame, options.target,
                             options.precision, batch_size)
    origins = pd.date_range(options.start_origin, options.end_origin, freq="MS")
    if options.validation_run:
        origins = pd.date_range("2018-01-01", "2020-12-01", freq="MS")
    if options.smoke_test:
        origins = origins[:1]
    horizon = 2 if options.smoke_test else 12
    output = (PROCESSED / "phase4c" / "validation" if options.validation_run else OUTPUT)
    if options.smoke_test:
        output = PROCESSED / "phase4c" / "smoke"
    output.mkdir(parents=True, exist_ok=True)
    timings = []
    benchmark_sizes = ([int(value) for value in options.benchmark_batch_sizes.split(",")]
                       if options.benchmark_batch_sizes else [batch_size])
    for origin in origins:
        context = frame[frame["month"] <= origin].copy()
        context = context.rename(columns={"geography": "item_id", "month": "timestamp", options.target: "target"})
        columns = ["item_id", "timestamp", "target"]
        future = None
        if options.experiment == "E12":
            columns += list(MONTHLY_WEATHER_FEATURES)
            months = pd.date_range(origin + pd.DateOffset(months=1), periods=horizon, freq="MS")
            future = frame[frame["month"].isin(months)].rename(columns={"geography": "item_id", "month": "timestamp"})
            future = future[["item_id", "timestamp", *MONTHLY_WEATHER_FEATURES]]
        path = output / f"{options.experiment}_{options.target}_{origin:%Y%m}.parquet"
        if options.resume and path.exists() and not options.benchmark_batch_sizes:
            print(f"skipping validated partition {path.name}", flush=True)
            continue
        prediction = None
        for candidate_batch_size in benchmark_sizes:
            import torch
            torch.cuda.reset_peak_memory_stats()
            started = time.perf_counter()
            candidate = pipeline.predict_df(
                context[columns], future_df=future, prediction_length=horizon,
                quantile_levels=phase4b["chronos2"]["quantiles"], id_column="item_id",
                timestamp_column="timestamp", target="target", batch_size=candidate_batch_size,
            )
            duration = time.perf_counter() - started
            timings.append({"origin": f"{origin:%Y-%m-%d}", "seconds": duration,
                            "rows": len(candidate), "batch_size": candidate_batch_size,
                            "precision": options.precision,
                            "peak_vram_bytes": int(torch.cuda.max_memory_allocated())})
            print(json.dumps(timings[-1]), flush=True)
            prediction = candidate
        assert prediction is not None
        prediction = prediction.rename(columns={"item_id": "geography", "timestamp": "target_month", "0.1": "p10", "0.5": "p50", "0.9": "p90"})
        actual = frame[["geography", "month", options.target]].rename(columns={"month": "target_month", options.target: "actual"})
        prediction = prediction.merge(actual, on=["geography", "target_month"], how="left", validate="one_to_one")
        prediction["experiment"] = options.experiment
        prediction["model"] = {"E11": "chronos2_zero_shot", "E12": "chronos2_oracle", "E13": "chronos2_lora"}[options.experiment]
        prediction["target_name"] = options.target
        prediction["origin"] = origin
        prediction["horizon"] = prediction.groupby("geography", observed=True).cumcount() + 1
        prediction["frequency"] = "monthly"; prediction["forecast_path"] = "direct_monthly"
        prediction["oracle_weather"] = options.experiment == "E12"
        prediction["forecast_status"] = np.where(prediction["actual"].notna(), "complete", "incomplete_horizon")
        if not options.benchmark_batch_sizes:
            temporary = path.with_suffix(".parquet.tmp")
            prediction.to_parquet(temporary, index=False, compression="zstd")
            temporary.replace(path)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{options.experiment}_{options.target}_{'smoke' if options.smoke_test else 'execution'}.json").write_text(
        json.dumps({"status": "complete", "model_revision": phase4b["chronos2"]["revision"],
                    "torch_environment": {"precision": options.precision, "batch_size": batch_size,
                                          "hip_visible_devices": os.environ.get("HIP_VISIBLE_DEVICES")},
                    "timings": timings}, indent=2) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
