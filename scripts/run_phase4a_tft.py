#!/usr/bin/env python3
"""Train E6/E7 small oracle-weather TFTs on the verified AMD ROCm GPU."""

from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path

import lightning as L
import pandas as pd
import torch
from lightning.pytorch import Trainer
from lightning.pytorch.callbacks import EarlyStopping, ModelCheckpoint
from lightning.pytorch.loggers import CSVLogger

from forecasting.weather_oracle import prepare_daily, prepare_monthly
from forecasting.weather_tft import assert_finite_frame, build_datasets, build_model, prepare_frame

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
INTEGRATED = PROCESSED / "weather" / "regional" / "integrated"


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", choices=("E6", "E7"), required=True)
    parser.add_argument("--target", choices=("total_energy_mwh", "peak_demand_mw"), required=True)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--persistent-workers", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--pin-memory", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--prefetch-factor", type=int, choices=(2, 4), default=2)
    parser.add_argument("--precision", choices=("32-true", "bf16-mixed"))
    parser.add_argument("--deterministic", choices=("warn", "true", "false"), default="warn")
    return parser.parse_args()


def main() -> int:
    options = args()
    config = json.loads((ROOT / "config" / "phase4a.json").read_text())
    tft = config["tft"]
    if options.num_workers < 0:
        raise ValueError("num-workers cannot be negative")
    if options.persistent_workers and options.num_workers == 0:
        raise ValueError("persistent-workers requires num-workers > 0")
    L.seed_everything(int(tft["seed"]), workers=True)
    if not torch.cuda.is_available() or "AMD Radeon AI PRO R9700" not in torch.cuda.get_device_name(0):
        raise RuntimeError("AMD R9700 ROCm GPU unavailable; CPU fallback is forbidden")
    if options.experiment == "E6":
        source = prepare_monthly(
            pd.read_parquet(INTEGRATED / "weather_demand_monthly.parquet"),
            pd.read_parquet(PROCESSED / "ercot_monthly_targets.parquet"),
        )
        frequency, encoder, decoder, batch = "monthly", tft["encoder_months"], tft["decoder_months"], tft["batch_size_monthly"]
    else:
        source = prepare_daily(pd.read_parquet(INTEGRATED / "weather_demand_daily.parquet"), config["completeness_threshold_pct"])
        source = source[source["target_valid"]]
        frequency, encoder, decoder, batch = "daily", tft["encoder_days"], tft["decoder_days"], tft["batch_size_daily"]
    batch = int(options.batch_size or batch)
    precision = options.precision or tft["precision"]
    deterministic: bool | str = {"warn": "warn", "true": True, "false": False}[options.deterministic]
    if options.smoke_test:
        source = source[source["geography"] == "ERCOT_SYSTEM"].copy()
        time_column = "month" if frequency == "monthly" else "date"
        source = source[pd.to_datetime(source[time_column]) >= pd.Timestamp("2014-01-01")]
        decoder = 2 if frequency == "monthly" else 14
        encoder = min(encoder, 24 if frequency == "monthly" else 90)
    frame, known_categoricals, weather = prepare_frame(source, options.target, frequency)
    assert_finite_frame(frame, weather)
    training, validation = build_datasets(
        frame, known_categoricals, weather, int(encoder), int(decoder),
        config["splits"]["training_end"], config["splits"]["validation_end"],
    )
    loader_options = {
        "num_workers": options.num_workers, "pin_memory": options.pin_memory,
        "persistent_workers": options.persistent_workers,
    }
    if options.num_workers > 0:
        loader_options["prefetch_factor"] = options.prefetch_factor
    train_loader = training.to_dataloader(train=True, batch_size=batch, **loader_options)
    val_loader = validation.to_dataloader(train=False, batch_size=batch, **loader_options)
    model = build_model(training, tft)
    output = ROOT / "models" / "phase4a" / options.experiment / options.target
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = ModelCheckpoint(dirpath=output, filename="{epoch:03d}-{val_loss:.6f}", monitor="val_loss", mode="min", save_top_k=1, save_last=True)
    callbacks = [checkpoint, EarlyStopping(monitor="val_loss", patience=int(tft["early_stopping_patience"]), mode="min")]
    trainer = Trainer(
        accelerator="gpu", devices=[0], precision=precision,
        max_epochs=1 if options.smoke_test else int(tft["max_epochs"]),
        gradient_clip_val=float(tft["gradient_clip_val"]), deterministic=deterministic,
        callbacks=callbacks, logger=CSVLogger(output, name="logs"), log_every_n_steps=1,
    )
    torch.cuda.reset_peak_memory_stats(0)
    started = time.perf_counter()
    checkpoint_path = output / "last.ckpt"
    trainer.fit(model, train_loader, val_loader, ckpt_path=str(checkpoint_path) if options.resume and checkpoint_path.exists() else None)
    duration = time.perf_counter() - started
    final_validation_loss = float(trainer.callback_metrics["val_loss"].detach().cpu())
    best_validation_loss = float(checkpoint.best_model_score.detach().cpu()) if checkpoint.best_model_score is not None else final_validation_loss
    if not torch.isfinite(torch.tensor(best_validation_loss)):
        raise RuntimeError("non-finite validation loss")
    report = {
        "status": "smoke_passed" if options.smoke_test else "trained", "experiment": options.experiment,
        "target": options.target, "oracle_weather": True, "frequency": frequency,
        "training_sequences": len(training), "validation_sequences": len(validation),
        "parameters": sum(parameter.numel() for parameter in model.parameters()),
        "validation_loss": best_validation_loss, "final_validation_loss": final_validation_loss,
        "best_checkpoint": checkpoint.best_model_path, "epochs_completed": int(trainer.current_epoch) + 1,
        "duration_seconds": duration,
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(0),
        "peak_reserved_bytes": torch.cuda.max_memory_reserved(0),
        "cpu_max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "torch": torch.__version__, "hip": torch.version.hip, "gpu": torch.cuda.get_device_name(0),
        "decoder_target_known": False, "weather_known_future": weather,
        "performance_configuration": {
            "batch_size": batch, "num_workers": options.num_workers,
            "persistent_workers": options.persistent_workers, "pin_memory": options.pin_memory,
            "prefetch_factor": options.prefetch_factor if options.num_workers > 0 else None,
            "precision": precision, "deterministic": options.deterministic,
        },
    }
    reports = ROOT / "reports" / "phase4a"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"{options.experiment}_{options.target}_{'smoke' if options.smoke_test else 'training'}.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
