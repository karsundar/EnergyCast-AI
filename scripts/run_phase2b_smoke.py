#!/usr/bin/env python3
"""Run a three-epoch AMD GPU TFT smoke test on actual ERCOT monthly data."""

from __future__ import annotations

import json
import math
import resource
import sys
import time
from pathlib import Path
from typing import Any

import lightning as L
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import torch
from lightning.pytorch import Trainer
from lightning.pytorch.callbacks import Callback, ModelCheckpoint
from lightning.pytorch.loggers import CSVLogger
from pytorch_forecasting import TemporalFusionTransformer

from forecasting.tft import (TFTDataConfig, assert_decoder_is_leakage_safe,
                             build_datasets, build_model, finite_gradients,
                             make_loaders, prepare_tft_frame)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "tft_models.json"
FEATURES_PATH = ROOT / "data" / "processed" / "ercot_monthly_features.parquet"
OUTPUT_ROOT = ROOT / "models" / "tft" / "smoke"
REPORT_PATH = ROOT / "reports" / "tft_smoke_report.json"
CHART_PATH = ROOT / "reports" / "charts" / "phase2b_tft_smoke_loss.png"


class FiniteTrainingGuard(Callback):
    def __init__(self) -> None:
        self.epoch_metrics: list[dict[str, float]] = []

    def on_after_backward(self, trainer: Trainer, pl_module: L.LightningModule) -> None:
        if not finite_gradients(pl_module):
            raise RuntimeError("non-finite or missing gradients detected")

    def on_train_epoch_end(self, trainer: Trainer, pl_module: L.LightningModule) -> None:
        metrics: dict[str, float] = {"epoch": float(trainer.current_epoch)}
        for name in ("train_loss_epoch", "val_loss"):
            value = trainer.callback_metrics.get(name)
            if value is not None:
                numeric = float(value.detach().cpu())
                if not math.isfinite(numeric):
                    raise RuntimeError(f"non-finite {name}: {numeric}")
                metrics[name] = numeric
        self.epoch_metrics.append(metrics)


def move_to_device(value: Any, device: torch.device) -> Any:
    if isinstance(value, torch.Tensor):
        return value.to(device)
    if isinstance(value, dict):
        return {key: move_to_device(item, device) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(move_to_device(item, device) for item in value)
    return value


def run_target(target_name: str, config: dict[str, Any]) -> dict[str, Any]:
    common = config["common"]
    small = config["small"]
    data_config = TFTDataConfig(
        encoder_length=int(small["encoder_length"]),
        prediction_length=int(common["prediction_length"]),
        batch_size=int(common["batch_size"]),
    )
    features = pd.read_parquet(FEATURES_PATH)
    frame = prepare_tft_frame(features, target_name)
    training, validation, training_cutoff = build_datasets(frame, data_config)
    assert_decoder_is_leakage_safe(training)
    train_loader, validation_loader = make_loaders(training, validation, data_config.batch_size)
    model = build_model(training, small, common)
    guard = FiniteTrainingGuard()
    target_dir = OUTPUT_ROOT / target_name
    checkpoint = ModelCheckpoint(
        dirpath=target_dir,
        filename="{epoch:02d}-{val_loss:.6f}",
        monitor="val_loss",
        mode="min",
        save_top_k=1,
        save_last=True,
    )
    logger = CSVLogger(save_dir=target_dir, name="logs")
    trainer = Trainer(
        accelerator="gpu",
        devices=[0],
        precision=str(common["precision"]),
        max_epochs=3,
        gradient_clip_val=float(common["gradient_clip_val"]),
        # ROCm does not provide a deterministic backward kernel for the TFT's
        # linear upsampling operation. Keep seeded best-effort reproducibility
        # while allowing that operation and surfacing a warning.
        deterministic="warn",
        callbacks=[guard, checkpoint],
        logger=logger,
        enable_progress_bar=True,
        log_every_n_steps=1,
    )
    torch.cuda.set_device(0)
    torch.cuda.reset_peak_memory_stats(0)
    started = time.perf_counter()
    trainer.fit(model, train_dataloaders=train_loader, val_dataloaders=validation_loader)
    duration = time.perf_counter() - started
    if trainer.device_ids != [0]:
        raise RuntimeError(f"training did not use GPU device 0: {trainer.device_ids}")
    best_path = checkpoint.best_model_path
    if not best_path or not Path(best_path).is_file():
        raise RuntimeError("best checkpoint was not saved")
    reloaded = TemporalFusionTransformer.load_from_checkpoint(best_path).to("cuda:0").eval()
    batch_x, _ = next(iter(validation_loader))
    batch_x = move_to_device(batch_x, torch.device("cuda:0"))
    with torch.no_grad():
        predictions = reloaded(batch_x).prediction
    torch.cuda.synchronize(0)
    expected = (len(validation), data_config.prediction_length, len(common["quantiles"]))
    actual = tuple(int(value) for value in predictions.shape)
    if actual != expected:
        raise RuntimeError(f"unexpected prediction shape: expected {expected}, got {actual}")
    if not bool(torch.isfinite(predictions).all()):
        raise RuntimeError("smoke forecasts contain NaN or infinite values")
    return {
        "target_name": target_name,
        "training_cutoff": str(training_cutoff.date()),
        "validation_period": "2020-01 through 2020-12",
        "training_sequences": len(training),
        "validation_sequences": len(validation),
        "parameters": sum(parameter.numel() for parameter in model.parameters()),
        "duration_seconds": duration,
        "seconds_per_epoch": duration / 3,
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(0),
        "peak_reserved_bytes": torch.cuda.max_memory_reserved(0),
        "cpu_max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "best_checkpoint": str(Path(best_path).relative_to(ROOT)),
        "checkpoint_bytes": Path(best_path).stat().st_size,
        "prediction_shape": actual,
        "prediction_finite": True,
        "gradient_finite": True,
        "epoch_metrics": guard.epoch_metrics,
    }


def main() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    seed = int(config["common"]["seed"])
    L.seed_everything(seed, workers=True)
    if not torch.cuda.is_available():
        raise RuntimeError("ROCm GPU unavailable; CPU fallback is forbidden")
    if torch.cuda.get_device_name(0) != "AMD Radeon AI PRO R9700":
        raise RuntimeError(f"unexpected GPU device 0: {torch.cuda.get_device_name(0)}")
    results = [run_target(target, config) for target in ("total_energy_mwh", "peak_demand_mw")]
    report = {
        "status": "passed",
        "torch_version": torch.__version__,
        "hip_version": torch.version.hip,
        "gpu": torch.cuda.get_device_name(0),
        "precision": config["common"]["precision"],
        "epochs": 3,
        "architecture": "small",
        "decoder_target_derived_features": [],
        "results": results,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    CHART_PATH.parent.mkdir(parents=True, exist_ok=True)
    figure, axes = plt.subplots(1, 2, figsize=(10, 4))
    for axis, result in zip(axes, results, strict=True):
        metrics = result["epoch_metrics"]
        axis.plot([row["epoch"] + 1 for row in metrics], [row.get("train_loss_epoch", float("nan")) for row in metrics], marker="o", label="train")
        axis.set(title=result["target_name"], xlabel="Epoch", ylabel="Quantile loss")
        axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(CHART_PATH, dpi=140)
    plt.close(figure)
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
