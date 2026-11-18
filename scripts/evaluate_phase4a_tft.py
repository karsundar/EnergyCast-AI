#!/usr/bin/env python3
"""Evaluate saved Phase 4A E6/E7 oracle-weather TFT checkpoints without training."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from pytorch_forecasting import TemporalFusionTransformer, TimeSeriesDataSet

from forecasting.weather_oracle import prepare_daily, prepare_monthly
from forecasting.weather_tft import build_datasets, prepare_frame

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
INTEGRATED = PROCESSED / "weather" / "regional" / "integrated"
OUTPUT = PROCESSED / "phase4a" / "predictions"
REPORTS = ROOT / "reports" / "phase4a"
TARGETS = ("total_energy_mwh", "peak_demand_mw")


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiments", default="E6,E7")
    parser.add_argument("--targets", default=",".join(TARGETS))
    parser.add_argument("--start-origin", default="2020-12-01")
    parser.add_argument("--end-origin", default="2025-04-01")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--summarize-only", action="store_true")
    return parser.parse_args()


def best_checkpoint(experiment: str, target: str) -> Path:
    directory = ROOT / "models" / "phase4a" / experiment / target
    pattern = re.compile(r"epoch=(\d+)-val_loss=([0-9.]+)\.ckpt$")
    candidates = []
    for path in directory.glob("epoch=*-val_loss=*.ckpt"):
        match = pattern.match(path.name)
        if match:
            candidates.append((float(match.group(2)), path))
    if not candidates:
        raise FileNotFoundError(f"no validation-selected checkpoint under {directory}")
    return min(candidates, key=lambda item: item[0])[1]


def source_frame(experiment: str, target: str, config: dict[str, Any]) -> tuple[pd.DataFrame, Any, int]:
    tft = config["tft"]
    if experiment == "E6":
        source = prepare_monthly(
            pd.read_parquet(INTEGRATED / "weather_demand_monthly.parquet"),
            pd.read_parquet(PROCESSED / "ercot_monthly_targets.parquet"),
        )
        frequency, encoder, decoder = "monthly", int(tft["encoder_months"]), int(tft["decoder_months"])
    else:
        source = prepare_daily(
            pd.read_parquet(INTEGRATED / "weather_demand_daily.parquet"),
            float(config["completeness_threshold_pct"]),
        )
        source = source[source["target_valid"]]
        frequency, encoder, decoder = "daily", int(tft["encoder_days"]), int(tft["decoder_days"])
    frame, categoricals, weather = prepare_frame(source, target, frequency)
    training, _ = build_datasets(
        frame, categoricals, weather, encoder, decoder,
        config["splits"]["training_end"], config["splits"]["validation_end"],
    )
    return frame, training, decoder


def test_dataset(training: Any, frame: pd.DataFrame, experiment: str,
                 start_origin: pd.Timestamp, end_origin: pd.Timestamp) -> TimeSeriesDataSet:
    if experiment == "E6":
        first_start = start_origin + pd.DateOffset(months=1)
        first_end = end_origin + pd.DateOffset(months=1)
    else:
        first_start = start_origin + pd.offsets.MonthEnd(0) + pd.Timedelta(days=1)
        first_end = end_origin + pd.offsets.MonthEnd(0) + pd.Timedelta(days=1)
    first_dates = pd.date_range(first_start, first_end, freq="MS")
    date_by_idx = frame.drop_duplicates("time_idx").set_index("time_idx")["timestamp"]
    first_idx = int(frame.loc[frame["timestamp"] == first_dates[0], "time_idx"].min())
    dataset = TimeSeriesDataSet.from_dataset(
        training, frame, min_prediction_idx=first_idx, predict=False, stop_randomization=True,
    )
    allowed = set(first_dates)
    dataset = dataset.filter(
        lambda decoded: decoded["time_idx_first_prediction"].map(date_by_idx).isin(allowed)
    )
    expected = len(first_dates) * frame["geography"].nunique()
    if len(dataset) != expected:
        raise RuntimeError(f"selected {len(dataset)} TFT sequences; expected {expected}")
    return dataset


def daily_to_monthly(frame: pd.DataFrame, target: str) -> pd.DataFrame:
    keys = ["experiment", "model", "target_name", "origin", "target_month", "horizon", "geography", "oracle_weather"]
    grouped = frame.groupby(keys, observed=True)[["p10", "p50", "p90", "actual"]]
    result = (grouped.sum(min_count=1) if target == "total_energy_mwh" else grouped.max()).reset_index()
    result["frequency"] = "monthly"
    result["forecast_path"] = "aggregated_daily"
    result["forecast_status"] = np.where(result["actual"].notna(), "complete", "incomplete_horizon")
    result["interval_aggregation"] = "uncalibrated_component_quantiles"
    return result


def prediction_frame(result: Any, frame: pd.DataFrame, experiment: str, target: str) -> pd.DataFrame:
    values = result.output.detach().cpu().numpy()
    index = result.index.reset_index(drop=True)
    lookup = frame.set_index(["geography", "time_idx"])[["timestamp", "target"]]
    records: list[dict[str, Any]] = []
    for row_number, row in index.iterrows():
        geography = str(row["geography"])
        first_idx = int(row["time_idx"])
        first_date = pd.Timestamp(lookup.loc[(geography, first_idx), "timestamp"])
        origin = (first_date - pd.DateOffset(months=1)).to_period("M").to_timestamp()
        for offset in range(values.shape[1]):
            target_idx = first_idx + offset
            actual_row = lookup.loc[(geography, target_idx)]
            timestamp = pd.Timestamp(actual_row["timestamp"])
            target_month = timestamp.to_period("M").to_timestamp()
            horizon = (target_month.year - origin.year) * 12 + target_month.month - origin.month
            if not 1 <= horizon <= 12:
                continue
            records.append({
                "experiment": experiment, "model": "tft_monthly_oracle" if experiment == "E6" else "tft_daily_oracle",
                "frequency": "monthly" if experiment == "E6" else "daily",
                "forecast_path": "direct_monthly" if experiment == "E6" else "daily_direct",
                "target_name": target, "origin": origin, "target_month": target_month,
                "target_date": timestamp if experiment == "E7" else pd.NaT,
                "horizon": horizon, "geography": geography,
                "p10": float(values[row_number, offset, 0]),
                "p50": float(values[row_number, offset, 1]),
                "p90": float(values[row_number, offset, 2]),
                "actual": float(actual_row["target"]), "oracle_weather": True,
                "forecast_status": "complete", "interval_aggregation": "native",
            })
    predictions = pd.DataFrame.from_records(records)
    return daily_to_monthly(predictions, target) if experiment == "E7" else predictions


def summarize_probabilistic() -> pd.DataFrame:
    paths = sorted(OUTPUT.glob("E[67]_*.parquet"))
    if not paths:
        raise RuntimeError("no E6/E7 prediction partitions found")
    frame = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
    frame = frame[frame["forecast_status"] == "complete"].copy()
    records: list[dict[str, Any]] = []
    groupings = {
        "overall": ["experiment", "target_name", "forecast_path"],
        "geography": ["experiment", "target_name", "forecast_path", "geography"],
        "horizon": ["experiment", "target_name", "forecast_path", "horizon"],
    }
    for breakdown, columns in groupings.items():
        for keys, group in frame.groupby(columns, observed=True):
            keys = keys if isinstance(keys, tuple) else (keys,)
            row: dict[str, Any] = {"breakdown": breakdown, **dict(zip(columns, keys, strict=True))}
            row.update({
                "observations": len(group),
                "p10_p90_coverage": float(group["actual"].between(group["p10"], group["p90"]).mean()),
                "mean_interval_width": float((group["p90"] - group["p10"]).mean()),
                "quantile_crossing_rate": float(((group["p10"] > group["p50"]) | (group["p50"] > group["p90"])).mean()),
                "interval_aggregation": "native" if group["experiment"].iloc[0] == "E6" else "uncalibrated_component_quantiles",
            })
            for quantile, column in ((0.1, "p10"), (0.5, "p50"), (0.9, "p90")):
                error = group["actual"] - group[column]
                row[f"q{int(quantile * 100)}_pinball_loss"] = float(np.maximum(quantile * error, (quantile - 1) * error).mean())
            records.append(row)
    metrics = pd.DataFrame.from_records(records)
    metrics.to_csv(REPORTS / "phase4a_tft_probabilistic_metrics.csv", index=False)
    return metrics


def main() -> int:
    options = arguments()
    experiments = options.experiments.split(",")
    targets = options.targets.split(",")
    if set(experiments) - {"E6", "E7"} or set(targets) - set(TARGETS):
        raise ValueError("supported experiments are E6/E7 and the two canonical targets")
    if options.summarize_only:
        metrics = summarize_probabilistic()
        print(json.dumps({"status": "complete", "metric_rows": len(metrics)}, indent=2))
        return 0
    if not torch.cuda.is_available():
        raise RuntimeError("ROCm GPU unavailable; CPU fallback is forbidden")
    config = json.loads((ROOT / "config" / "phase4a.json").read_text())
    OUTPUT.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    performance = []
    for experiment in experiments:
        for target in targets:
            checkpoint = best_checkpoint(experiment, target)
            frame, training, _ = source_frame(experiment, target, config)
            dataset = test_dataset(training, frame, experiment, pd.Timestamp(options.start_origin), pd.Timestamp(options.end_origin))
            loader = dataset.to_dataloader(train=False, batch_size=options.batch_size, num_workers=0)
            model = TemporalFusionTransformer.load_from_checkpoint(checkpoint).to("cuda:0").eval()
            started = time.perf_counter()
            result = model.predict(
                loader, mode="quantiles", return_index=True,
                trainer_kwargs={"accelerator": "gpu", "devices": [0], "precision": "32-true"},
            )
            predictions = prediction_frame(result, frame, experiment, target)
            if not np.isfinite(predictions[["p10", "p50", "p90", "actual"]].to_numpy()).all():
                raise RuntimeError(f"non-finite prediction in {experiment} {target}")
            for origin, part in predictions.groupby("origin", observed=True):
                path = OUTPUT / f"{experiment}_{target}_{pd.Timestamp(origin):%Y%m}.parquet"
                temporary = path.with_suffix(".parquet.tmp")
                part.to_parquet(temporary, index=False, compression="zstd")
                temporary.replace(path)
            performance.append({
                "experiment": experiment, "target": target, "checkpoint": str(checkpoint.relative_to(ROOT)),
                "sequences": len(dataset), "prediction_rows": len(predictions),
                "inference_seconds": time.perf_counter() - started,
            })
            print(json.dumps(performance[-1]), flush=True)
    report = {"status": "complete", "oracle_weather": True, "performance": performance}
    (REPORTS / "tft_evaluation_performance.json").write_text(json.dumps(report, indent=2) + "\n")
    summarize_probabilistic()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
