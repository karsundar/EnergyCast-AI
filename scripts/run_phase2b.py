#!/usr/bin/env python3
"""Train and evaluate validation-selected small TFT models on the AMD GPU."""

from __future__ import annotations

import json
import math
import resource
import time
from pathlib import Path
from typing import Any

import lightning as L
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
from lightning.pytorch import Trainer
from lightning.pytorch.callbacks import EarlyStopping, ModelCheckpoint
from lightning.pytorch.loggers import CSVLogger
from pytorch_forecasting import TemporalFusionTransformer

from forecasting.tft import (TFTDataConfig, assert_decoder_is_leakage_safe,
                             build_backtest_datasets, build_model, make_loaders,
                             prepare_tft_frame)


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
MODEL_ROOT = ROOT / "models" / "tft" / "small"


def prediction_frame(result: Any, source: pd.DataFrame, target_name: str) -> pd.DataFrame:
    values = result.output.detach().cpu().numpy()
    index = result.index.reset_index(drop=True)
    records: list[dict[str, Any]] = []
    lookup = source.set_index(["geography", "time_idx"])[["month", "target_value"]]
    for row_number, row in index.iterrows():
        geography = str(row["geography"])
        first_idx = int(row["time_idx"])
        origin_month = pd.Timestamp(lookup.loc[(geography, first_idx - 1), "month"])
        for offset in range(values.shape[1]):
            target_idx = first_idx + offset
            actual_row = lookup.loc[(geography, target_idx)]
            records.append({
                "model": "tft_small",
                "target_name": target_name,
                "geography": geography,
                "forecast_origin": origin_month,
                "target_month": pd.Timestamp(actual_row["month"]),
                "horizon": offset + 1,
                "actual": float(actual_row["target_value"]),
                "p10": float(values[row_number, offset, 0]),
                "p50": float(values[row_number, offset, 1]),
                "p90": float(values[row_number, offset, 2]),
                "forecast_status": "complete",
            })
    return pd.DataFrame.from_records(records)


def metric_frame(predictions: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    groupings = {
        "overall": ["target_name"],
        "horizon": ["target_name", "horizon"],
        "geography": ["target_name", "geography"],
        "geography_horizon": ["target_name", "geography", "horizon"],
    }
    for breakdown, columns in groupings.items():
        for keys, frame in predictions.groupby(columns, observed=True):
            keys = keys if isinstance(keys, tuple) else (keys,)
            values = dict(zip(columns, keys, strict=True))
            error = frame["p50"] - frame["actual"]
            absolute = error.abs()
            interval_width = frame["p90"] - frame["p10"]
            records.append({
                "model": "tft_small",
                "breakdown": breakdown,
                **values,
                "observations": len(frame),
                "mae": float(absolute.mean()),
                "rmse": float(np.sqrt(np.mean(np.square(error)))),
                "mape": float((absolute / frame["actual"].abs()).mean() * 100),
                "wape": float(absolute.sum() / frame["actual"].abs().sum() * 100),
                "forecast_bias": float(error.mean()),
                "interval_coverage": float(((frame["actual"] >= frame["p10"]) & (frame["actual"] <= frame["p90"])).mean()),
                "interval_width": float(interval_width.mean()),
                "quantile_crossing_rate": float(((frame["p10"] > frame["p50"]) | (frame["p50"] > frame["p90"])).mean()),
                "p10_quantile_loss": float((2 * np.maximum(0.1 * (frame["actual"] - frame["p10"]), 0.9 * (frame["p10"] - frame["actual"]))).mean()),
                "p50_quantile_loss": float((2 * np.maximum(0.5 * (frame["actual"] - frame["p50"]), 0.5 * (frame["p50"] - frame["actual"]))).mean()),
                "p90_quantile_loss": float((2 * np.maximum(0.9 * (frame["actual"] - frame["p90"]), 0.1 * (frame["p90"] - frame["actual"]))).mean()),
            })
    return pd.DataFrame.from_records(records)


def train_target(target_name: str, configuration: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    common = configuration["common"]
    small = configuration["small"]
    data_config = TFTDataConfig(
        encoder_length=int(small["encoder_length"]),
        prediction_length=int(common["prediction_length"]),
        batch_size=int(common["batch_size"]),
    )
    source = prepare_tft_frame(pd.read_parquet(PROCESSED / "ercot_monthly_features.parquet"), target_name)
    training, validation, test = build_backtest_datasets(source, data_config)
    assert_decoder_is_leakage_safe(training)
    train_loader, validation_loader = make_loaders(training, validation, data_config.batch_size)
    test_loader = test.to_dataloader(train=False, batch_size=data_config.batch_size, num_workers=0)
    model = build_model(training, small, common)
    target_dir = MODEL_ROOT / target_name
    checkpoint = ModelCheckpoint(
        dirpath=target_dir, filename="{epoch:03d}-{val_loss:.6f}", monitor="val_loss", mode="min", save_top_k=1, save_last=True
    )
    stopping = EarlyStopping(monitor="val_loss", mode="min", patience=int(common["early_stopping_patience"]))
    logger = CSVLogger(save_dir=target_dir, name="logs")
    existing = sorted(target_dir.glob("epoch=*-val_loss=*.ckpt"))
    reused_checkpoint = bool(existing)
    if existing:
        best_path = min(existing, key=lambda path: float(path.stem.split("val_loss=")[1].split("-")[0]))
        best_score = float(best_path.stem.split("val_loss=")[1].split("-")[0])
        duration = None
        epochs_completed = None
    else:
        trainer = Trainer(
            accelerator="gpu", devices=[0], precision=str(common["precision"]),
            max_epochs=int(common["max_epochs"]), gradient_clip_val=float(common["gradient_clip_val"]),
            deterministic="warn", callbacks=[checkpoint, stopping], logger=logger,
            log_every_n_steps=1, enable_progress_bar=True,
        )
        torch.cuda.reset_peak_memory_stats(0)
        started = time.perf_counter()
        trainer.fit(model, train_dataloaders=train_loader, val_dataloaders=validation_loader)
        duration = time.perf_counter() - started
        if not checkpoint.best_model_path:
            raise RuntimeError("no best checkpoint was produced")
        best_path = Path(checkpoint.best_model_path)
        best_score = float(checkpoint.best_model_score)
        epochs_completed = int(trainer.current_epoch + 1)
    best = TemporalFusionTransformer.load_from_checkpoint(best_path)
    inference_started = time.perf_counter()
    result = best.predict(
        test_loader, mode="quantiles", return_index=True,
        trainer_kwargs={"accelerator": "gpu", "devices": [0], "precision": str(common["precision"])},
    )
    inference_duration = time.perf_counter() - inference_started
    predictions = prediction_frame(result, source, target_name)
    numeric = predictions[["actual", "p10", "p50", "p90"]].to_numpy()
    if not np.isfinite(numeric).all():
        raise RuntimeError("non-finite backtest prediction detected")
    performance = {
        "target_name": target_name,
        "best_checkpoint": str(best_path.relative_to(ROOT)),
        "best_validation_loss": best_score,
        "epochs_completed": epochs_completed,
        "reused_checkpoint": reused_checkpoint,
        "training_duration_seconds": duration,
        "average_epoch_seconds": None if duration is None else duration / max(epochs_completed or 1, 1),
        "inference_duration_seconds": inference_duration,
        "parameters": sum(parameter.numel() for parameter in best.parameters()),
        "checkpoint_bytes": best_path.stat().st_size,
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(0),
        "peak_reserved_bytes": torch.cuda.max_memory_reserved(0),
        "cpu_max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "training_sequences": len(training),
        "validation_sequences": len(validation),
        "test_sequences": len(test),
    }
    return predictions, performance


def main() -> int:
    configuration = json.loads((ROOT / "config" / "tft_models.json").read_text(encoding="utf-8"))
    L.seed_everything(int(configuration["common"]["seed"]), workers=True)
    if not torch.cuda.is_available() or torch.cuda.get_device_name(0) != "AMD Radeon AI PRO R9700":
        raise RuntimeError("required AMD R9700 GPU is unavailable; CPU fallback is forbidden")
    frames: list[pd.DataFrame] = []
    performance: list[dict[str, Any]] = []
    for target in ("total_energy_mwh", "peak_demand_mw"):
        predictions, target_performance = train_target(target, configuration)
        frames.append(predictions)
        performance.append(target_performance)
    combined = pd.concat(frames, ignore_index=True)
    metrics = metric_frame(combined)
    predictions_path = PROCESSED / "tft_backtest_predictions.parquet"
    metrics_path = REPORTS / "tft_metrics.parquet"
    combined.to_parquet(predictions_path, index=False, compression="zstd")
    metrics.to_parquet(metrics_path, index=False, compression="zstd")
    metrics.to_csv(REPORTS / "tft_metrics.csv", index=False)
    system = metrics[(metrics["breakdown"] == "geography") & (metrics["geography"] == "ERCOT_SYSTEM")]
    report = {
        "status": "complete", "architecture": "small", "selection_data": "2018-01 through 2020-12",
        "test_data": "2021-01 through latest complete 12-month horizon", "torch": torch.__version__,
        "hip": torch.version.hip, "gpu": torch.cuda.get_device_name(0), "performance": performance,
        "system_metrics": system.to_dict("records"), "decoder_target_derived_features": [],
    }
    (REPORTS / "tft_evaluation.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    figure, axes = plt.subplots(1, 2, figsize=(11, 4))
    for axis, target in zip(axes, ("total_energy_mwh", "peak_demand_mw"), strict=True):
        subset = metrics[(metrics["breakdown"] == "horizon") & (metrics["target_name"] == target)]
        axis.plot(subset["horizon"], subset["wape"], marker="o")
        axis.set(title=target, xlabel="Horizon", ylabel="WAPE (%)")
        axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(REPORTS / "charts" / "phase2b_tft_wape_by_horizon.png", dpi=140)
    plt.close(figure)
    print(json.dumps(report, indent=2, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
