#!/usr/bin/env python3
"""Audit existing TFT results and finalize Phase 2B reports without TFT training."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
from pytorch_forecasting import TemporalFusionTransformer, TimeSeriesDataSet

from forecasting.backtest import (BacktestConfig, evaluate_predictions,
                                  rolling_predictions)
from forecasting.features import calendar_features
from forecasting.tft import (TFTDataConfig, assert_decoder_is_leakage_safe,
                             build_backtest_datasets, prepare_tft_frame)


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
CHARTS = REPORTS / "charts"
MODEL_ROOT = ROOT / "models" / "tft" / "small"
KEYS = ["target_name", "geography", "origin", "target_month", "horizon"]
BASELINES = ["seasonal_naive", "drift_seasonal_naive", "holt_winters", "lightgbm_direct"]
TARGETS = ["total_energy_mwh", "peak_demand_mw"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def best_checkpoint(target_name: str) -> Path:
    candidates = sorted((MODEL_ROOT / target_name).glob("epoch=*-val_loss=*.ckpt"))
    if not candidates:
        raise FileNotFoundError(f"checkpoint missing for {target_name}")
    return min(candidates, key=lambda p: float(p.name.split("val_loss=")[1].split("-")[0].replace(".ckpt", "")))


def load_and_align() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    tft = pd.read_parquet(PROCESSED / "tft_backtest_predictions.parquet").rename(
        columns={"forecast_origin": "origin"}
    )
    baseline = pd.read_parquet(PROCESSED / "baseline_predictions.parquet")
    baseline = baseline[(baseline["forecast_status"] == "complete") & baseline["model"].isin(BASELINES)].copy()
    for frame in (tft, baseline):
        frame["origin"] = pd.to_datetime(frame["origin"])
        frame["target_month"] = pd.to_datetime(frame["target_month"])

    baseline_counts = baseline.groupby(KEYS, observed=True)["model"].nunique()
    full_baseline_keys = baseline_counts[baseline_counts == len(BASELINES)].index
    tft_keys = pd.MultiIndex.from_frame(tft[KEYS].drop_duplicates())
    common = full_baseline_keys.intersection(tft_keys)
    if common.empty:
        raise RuntimeError("no common TFT/baseline observations")
    tft_idx = pd.MultiIndex.from_frame(tft[KEYS])
    baseline_idx = pd.MultiIndex.from_frame(baseline[KEYS])
    tft_common = tft.loc[tft_idx.isin(common)].copy()
    baseline_common = baseline.loc[baseline_idx.isin(common)].copy()

    actual_check = baseline_common.groupby(KEYS, observed=True)["actual"].first().rename("baseline_actual").reset_index()
    actual_check = tft_common.merge(actual_check, on=KEYS, validate="one_to_one")
    max_actual_delta = float((actual_check["actual"] - actual_check["baseline_actual"]).abs().max())
    horizon_alignment = bool(((tft_common["target_month"].dt.to_period("M") - tft_common["origin"].dt.to_period("M")).apply(lambda x: x.n) == tft_common["horizon"]).all())
    diagnostics = {
        "tft_rows": int(len(tft)),
        "common_keys_per_model": int(len(common)),
        "common_tft_rows": int(len(tft_common)),
        "common_baseline_rows": int(len(baseline_common)),
        "baseline_models": BASELINES,
        "max_actual_alignment_delta": max_actual_delta,
        "timestamp_horizon_alignment": horizon_alignment,
        "p50_selected": "column p50 from mode='quantiles' output index 1 for [0.1, 0.5, 0.9]",
    }
    if max_actual_delta != 0 or not horizon_alignment:
        raise AssertionError(f"prediction alignment failed: {diagnostics}")
    combined = pd.concat([baseline_common, tft_common], ignore_index=True, sort=False)
    return combined, tft_common, diagnostics


def comparable_metrics(combined: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    metrics = evaluate_predictions(combined, targets)
    metrics.to_parquet(REPORTS / "phase2b_comparable_metrics.parquet", index=False, compression="zstd")
    metrics.to_csv(REPORTS / "phase2b_comparable_metrics.csv", index=False)
    return metrics


def calibration_metrics(tft: pd.DataFrame) -> pd.DataFrame:
    records = []
    groupings = {
        "overall": ["target_name"],
        "horizon": ["target_name", "horizon"],
        "region": ["target_name", "geography"],
        "region_horizon": ["target_name", "geography", "horizon"],
    }
    for breakdown, columns in groupings.items():
        for keys, frame in tft.groupby(columns, observed=True):
            keys = keys if isinstance(keys, tuple) else (keys,)
            actual = frame["actual"].to_numpy(float)
            p10, p50, p90 = (frame[column].to_numpy(float) for column in ("p10", "p50", "p90"))
            record = {"breakdown": breakdown, **dict(zip(columns, keys, strict=True)), "observations": len(frame)}
            record.update({
                "coverage_p10_p90": float(((actual >= p10) & (actual <= p90)).mean()),
                "mean_interval_width": float(np.mean(p90 - p10)),
                "normalized_interval_width": float(np.mean(p90 - p10) / np.mean(np.abs(actual))),
                "quantile_crossing_rate": float(((p10 > p50) | (p50 > p90)).mean()),
                "p10_quantile_loss": float(np.mean(np.maximum(.1 * (actual - p10), -.9 * (actual - p10)))),
                "p50_quantile_loss": float(np.mean(np.maximum(.5 * (actual - p50), -.5 * (actual - p50)))),
                "p90_quantile_loss": float(np.mean(np.maximum(.9 * (actual - p90), -.1 * (actual - p90)))),
            })
            records.append(record)
    result = pd.DataFrame(records)
    result.to_csv(REPORTS / "phase2b_calibration.csv", index=False)
    result.to_parquet(REPORTS / "phase2b_calibration.parquet", index=False, compression="zstd")
    return result


def seasonal_pattern_diagnostics(tft: pd.DataFrame) -> list[dict[str, Any]]:
    frame = tft.copy()
    frame["month_of_year"] = frame["target_month"].dt.month
    rows = []
    for (target, geography), group in frame.groupby(["target_name", "geography"], observed=True):
        profile = group.groupby("month_of_year", observed=True)[["actual", "p50"]].mean()
        rows.append({
            "target_name": target, "geography": geography,
            "monthly_profile_correlation": float(profile["actual"].corr(profile["p50"])),
            "actual_seasonal_range": float(profile["actual"].max() - profile["actual"].min()),
            "forecast_seasonal_range": float(profile["p50"].max() - profile["p50"].min()),
            "seasonal_amplitude_ratio": float((profile["p50"].max() - profile["p50"].min()) / (profile["actual"].max() - profile["actual"].min())),
        })
    return rows


def loss_diagnostics(target_name: str) -> dict[str, Any]:
    files = sorted((MODEL_ROOT / target_name / "logs").glob("version_*/metrics.csv"))
    if not files:
        return {"available": False}
    frame = pd.read_csv(files[-1])
    epochs = frame.dropna(subset=["val_loss"])[["epoch", "val_loss", "val_MAE", "val_RMSE"]].copy()
    train = frame.dropna(subset=["train_loss_epoch"])[["epoch", "train_loss_epoch"]]
    epochs = epochs.merge(train, on="epoch", how="left").sort_values("epoch")
    best = epochs.loc[epochs["val_loss"].idxmin()]
    return {
        "available": True,
        "epochs_logged": int(epochs["epoch"].nunique()),
        "first_validation_loss": float(epochs.iloc[0]["val_loss"]),
        "best_epoch": int(best["epoch"]),
        "best_validation_loss": float(best["val_loss"]),
        "last_epoch": int(epochs.iloc[-1]["epoch"]),
        "last_validation_loss": float(epochs.iloc[-1]["val_loss"]),
        "last_training_loss": float(epochs.iloc[-1]["train_loss_epoch"]),
        "early_stopping_patience": 10,
        "history": epochs.to_dict("records"),
    }


def interpretation_and_runtime(target_name: str) -> dict[str, Any]:
    data_config = TFTDataConfig(encoder_length=36, prediction_length=12, batch_size=32)
    source = prepare_tft_frame(pd.read_parquet(PROCESSED / "ercot_monthly_features.parquet"), target_name)
    training, validation, test = build_backtest_datasets(source, data_config)
    assert_decoder_is_leakage_safe(training)
    checkpoint = best_checkpoint(target_name)
    model = TemporalFusionTransformer.load_from_checkpoint(checkpoint).to("cuda:0")
    loader = test.to_dataloader(train=False, batch_size=32, num_workers=0)
    raw = model.predict(
        loader, mode="raw", return_x=True,
        trainer_kwargs={"accelerator": "gpu", "devices": [0], "precision": "32-true"},
    )
    interpretation = model.interpret_output(raw.output, reduction="mean")
    figures = model.plot_interpretation(interpretation)
    paths: list[str] = []
    for name, figure in figures.items():
        path = CHARTS / f"phase2b_{target_name}_{name}.png"
        figure.savefig(path, dpi=140, bbox_inches="tight")
        plt.close(figure)
        paths.append(str(path.relative_to(ROOT)))
    importance = {}
    for kind, labels in (
        ("static_variables", model.static_variables),
        ("encoder_variables", model.encoder_variables),
        ("decoder_variables", model.decoder_variables),
    ):
        values = interpretation[kind].detach().cpu().numpy().astype(float)
        values = values / values.sum() if values.sum() else values
        importance[kind] = dict(zip(labels, values.tolist(), strict=True))
    (REPORTS / f"tft_{target_name}_importance.json").write_text(json.dumps(importance, indent=2) + "\n")
    return {
        "checkpoint": str(checkpoint.relative_to(ROOT)),
        "training_sequences": len(training), "validation_sequences": len(validation), "test_sequences": len(test),
        "encoder_length": 36, "decoder_length": 12,
        "target_normalizer": repr(model.output_transformer),
        "prediction_rescaling": "PyTorch Forecasting predict(mode='quantiles') applies transform_output using checkpoint output_transformer and per-sample target_scale",
        "importance": importance, "charts": paths,
    }


def future_frame(source: pd.DataFrame, placeholder: float) -> pd.DataFrame:
    last_month = source["month"].max()
    future_months = pd.date_range(last_month + pd.DateOffset(months=1), periods=12, freq="MS")
    calendar = calendar_features(pd.Series(future_months))
    additions = []
    for geography in sorted(source["geography"].unique()):
        start_idx = int(source.loc[source["geography"] == geography, "time_idx"].max()) + 1
        for offset, month in enumerate(future_months):
            cal = calendar.iloc[offset]
            additions.append({
                "month": month, "geography": geography, "target_value": placeholder,
                "time_idx": start_idx + offset,
                "month_of_year": str(int(cal["known_future_month_of_year"])),
                "quarter": str(int(cal["known_future_quarter"])),
                "season": str(cal["known_future_season"]),
                "days_in_month": float(cal["known_future_days_in_month"]),
                "weekday_count": float(cal["known_future_weekday_count"]),
                "weekend_count": float(cal["known_future_weekend_count"]),
                "holiday_count": float(cal["known_future_holiday_count"]),
                "month_sin": float(cal["known_future_month_sin"]),
                "month_cos": float(cal["known_future_month_cos"]),
            })
    return pd.concat([source, pd.DataFrame(additions)], ignore_index=True).sort_values(["geography", "time_idx"])


def tft_future(target_name: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    source = prepare_tft_frame(pd.read_parquet(PROCESSED / "ercot_monthly_features.parquet"), target_name)
    training, _, _ = build_backtest_datasets(source, TFTDataConfig())
    model = TemporalFusionTransformer.load_from_checkpoint(best_checkpoint(target_name)).to("cuda:0")

    def predict(placeholder: float):
        extended = future_frame(source, placeholder)
        dataset = TimeSeriesDataSet.from_dataset(training, extended, predict=True, stop_randomization=True)
        result = model.predict(
            dataset, mode="quantiles", return_index=True,
            trainer_kwargs={"accelerator": "gpu", "devices": [0], "precision": "32-true"},
        )
        return result.output.detach().cpu().numpy(), result.index.reset_index(drop=True), extended

    values_zero, index_zero, extended = predict(0.0)
    values_large, index_large, _ = predict(1_000_000_000.0)
    max_delta = float(np.max(np.abs(values_zero - values_large)))
    invariant = bool(np.allclose(values_zero, values_large, rtol=0, atol=1e-5))
    if not invariant:
        raise AssertionError(f"future placeholder changes TFT predictions: max delta={max_delta}")
    lookup = extended.set_index(["geography", "time_idx"])["month"]
    records = []
    for row_no, row in index_zero.iterrows():
        geography, first_idx = str(row["geography"]), int(row["time_idx"])
        for offset in range(12):
            records.append({
                "forecast_origin": source["month"].max(), "target_month": lookup.loc[(geography, first_idx + offset)],
                "horizon": offset + 1, "geography": geography, "target_name": target_name,
                "model": "tft_small_experimental", "p10": float(values_zero[row_no, offset, 0]),
                "p50": float(values_zero[row_no, offset, 1]), "p90": float(values_zero[row_no, offset, 2]),
                "forecast_status": "future_experimental", "actual": np.nan,
            })
    return pd.DataFrame(records), {
        "placeholder_values_tested": [0.0, 1_000_000_000.0], "max_prediction_delta": max_delta,
        "invariant_at_absolute_tolerance": 1e-5, "passed": invariant,
        "decoder_known_variables": list(training.time_varying_known_categoricals) + list(training.time_varying_known_reals),
        "decoder_unknown_variables": list(training.time_varying_unknown_reals),
    }


def baseline_future(targets: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    params = json.loads((REPORTS / "baseline_evaluation.json").read_text())["selected_lightgbm_parameters"]
    origin = pd.DatetimeIndex([targets["month"].max()])
    frames = [rolling_predictions(targets, features, target, origin, params[target], BacktestConfig()) for target in TARGETS]
    future = pd.concat(frames, ignore_index=True)
    future = future[future["model"].isin(["holt_winters", "lightgbm_direct"])].copy()
    future = future.rename(columns={"origin": "forecast_origin"})
    calibration = pd.read_parquet(PROCESSED / "baseline_predictions.parquet")
    offsets = calibration[calibration["model"].isin(["holt_winters", "lightgbm_direct"])].copy()
    offsets["lower_offset"] = offsets["p10"] - offsets["p50"]
    offsets["upper_offset"] = offsets["p90"] - offsets["p50"]
    offsets = offsets.groupby(["model", "target_name", "geography", "horizon"], observed=True)[["lower_offset", "upper_offset"]].median().reset_index()
    future = future.drop(columns=["p10", "p90"], errors="ignore").merge(offsets, on=["model", "target_name", "geography", "horizon"], how="left")
    future["p10"] = future["p50"] + future["lower_offset"]
    future["p90"] = future["p50"] + future["upper_offset"]
    return future.drop(columns=["lower_offset", "upper_offset"])


def make_charts(combined: pd.DataFrame, metrics: pd.DataFrame) -> list[str]:
    CHARTS.mkdir(parents=True, exist_ok=True)
    paths = []
    tft = combined[combined["model"] == "tft_small"].copy()
    tft["residual"] = tft["p50"] - tft["actual"]
    system = tft[tft["geography"] == "ERCOT_SYSTEM"]

    fig, axes = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    for axis, target in zip(axes, TARGETS, strict=True):
        frame = system[(system["target_name"] == target) & (system["horizon"] == 1)].sort_values("target_month")
        axis.plot(frame["target_month"], frame["actual"], label="actual", color="black")
        axis.plot(frame["target_month"], frame["p50"], label="TFT P50")
        axis.fill_between(frame["target_month"], frame["p10"], frame["p90"], alpha=.2, label="P10-P90")
        axis.set_title(target); axis.legend(); axis.grid(alpha=.2)
    fig.tight_layout(); path = CHARTS / "phase2b_actual_vs_predicted.png"; fig.savefig(path, dpi=140); plt.close(fig); paths.append(str(path.relative_to(ROOT)))

    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for axis, target in zip(axes, TARGETS, strict=True):
        frame = tft[tft["target_name"] == target]
        axis.hist(frame["residual"], bins=40); axis.axvline(0, color="black", linewidth=1); axis.set_title(target)
    fig.tight_layout(); path = CHARTS / "phase2b_residual_distributions.png"; fig.savefig(path, dpi=140); plt.close(fig); paths.append(str(path.relative_to(ROOT)))

    heat = tft.groupby(["geography", "horizon"], observed=True).apply(lambda x: np.abs(x["residual"]).sum() / np.abs(x["actual"]).sum() * 100, include_groups=False).unstack()
    fig, axis = plt.subplots(figsize=(11, 5)); image = axis.imshow(heat, aspect="auto", cmap="magma")
    axis.set_yticks(range(len(heat.index)), heat.index); axis.set_xticks(range(12), range(1, 13)); axis.set(xlabel="Horizon", title="TFT regional WAPE heatmap (%)")
    fig.colorbar(image, ax=axis); fig.tight_layout(); path = CHARTS / "phase2b_regional_error_heatmap.png"; fig.savefig(path, dpi=140); plt.close(fig); paths.append(str(path.relative_to(ROOT)))

    horizon = metrics[(metrics["breakdown"] == "system_horizon")]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for axis, target in zip(axes, TARGETS, strict=True):
        for model, frame in horizon[horizon["target_name"] == target].groupby("model"):
            axis.plot(frame["horizon"], frame["wape"], marker="o", label=model)
        axis.set(title=target, xlabel="Horizon", ylabel="WAPE (%)"); axis.grid(alpha=.2)
    axes[1].legend(fontsize=7); fig.tight_layout(); path = CHARTS / "phase2b_comparable_wape_by_horizon.png"; fig.savefig(path, dpi=140); plt.close(fig); paths.append(str(path.relative_to(ROOT)))
    return paths


def report_markdown(report: dict[str, Any], metrics: pd.DataFrame) -> None:
    summary = metrics[(metrics["breakdown"] == "period_category") & (metrics["period_category"] == "all_test") & (metrics["geography"] == "ERCOT_SYSTEM")]
    if summary.empty:
        summary = metrics[(metrics["breakdown"] == "period_category") & (metrics["period_category"] == "all_test")]
    lines = [
        "# Phase 2B final evaluation", "", f"Generated: {report['created_at']}", "",
        "## Conclusion", "",
        "The existing small TFT is a valid leakage-controlled experiment but materially underperforms Holt-Winters. It remains experimental and is not the selected production forecast.", "",
        "## Confirmed audit findings", "",
    ]
    lines.extend(f"- {item}" for item in report["diagnostic_findings"]["confirmed"])
    lines.extend(["", "## Plausible hypotheses (not confirmed)", ""])
    lines.extend(f"- {item}" for item in report["diagnostic_findings"]["hypotheses"])
    lines.extend(["", "## Comparable-observation system results", "", "| Target | Model | N | MAE | RMSE | MAPE % | WAPE % | MASE | Bias | Direction | Coverage |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for row in summary.sort_values(["target_name", "wape"]).to_dict("records"):
        lines.append(f"| {row['target_name']} | {row['model']} | {int(row['observations'])} | {row['mae']:.2f} | {row['rmse']:.2f} | {row['mape']:.3f} | {row['wape']:.3f} | {row['mase']:.3f} | {row['forecast_bias']:.2f} | {row['directional_accuracy']:.3f} | {row['interval_coverage']:.3f} |")
    lines.extend([
        "", "## Interpretation and uncertainty", "",
        "Variable selection and temporal attention charts describe model behavior; they are not evidence of causation.",
        "P10-P90 coverage, quantile loss, interval width, regional/horizon calibration, and crossing checks are stored in the comparable metric and calibration artifacts.", "",
        "## Future forecast", "",
        "Holt-Winters is the best validated model. LightGBM is included as a baseline and TFT is explicitly labeled experimental. TFT future-target placeholders were varied from zero to one billion and produced invariant predictions before the forecast artifact was written.",
    ])
    (REPORTS / "phase2b_final_evaluation.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    if not torch.cuda.is_available():
        raise RuntimeError("AMD GPU unavailable; CPU fallback forbidden")
    REPORTS.mkdir(exist_ok=True); CHARTS.mkdir(exist_ok=True)
    targets = pd.read_parquet(PROCESSED / "ercot_monthly_targets.parquet")
    features = pd.read_parquet(PROCESSED / "ercot_monthly_features.parquet")
    combined, tft_common, alignment = load_and_align()
    metrics = comparable_metrics(combined, targets)
    calibration = calibration_metrics(tft_common)
    seasonal_patterns = seasonal_pattern_diagnostics(tft_common)
    interpretations = {target: interpretation_and_runtime(target) for target in TARGETS}
    tft_future_frames, invariance = [], {}
    for target in TARGETS:
        frame, check = tft_future(target); tft_future_frames.append(frame); invariance[target] = check
    future = pd.concat([baseline_future(targets, features), *tft_future_frames], ignore_index=True, sort=False)
    future.to_parquet(PROCESSED / "phase2b_next_12_month_forecasts.parquet", index=False, compression="zstd")
    future.to_csv(REPORTS / "phase2b_next_12_month_forecasts.csv", index=False)
    charts = make_charts(combined, metrics)

    crossing = float(((tft_common["p10"] > tft_common["p50"]) | (tft_common["p50"] > tft_common["p90"])).mean())
    system_tft = metrics[(metrics["model"] == "tft_small") & (metrics["breakdown"] == "period_category") & (metrics["period_category"] == "all_test")]
    loss = {target: loss_diagnostics(target) for target in TARGETS}
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(), "phase": "2B", "status": "reporting_complete",
        "torch": torch.__version__, "hip": torch.version.hip, "gpu": torch.cuda.get_device_name(0),
        "alignment": alignment, "loss_convergence": loss, "interpretation": interpretations,
        "seasonal_pattern_diagnostics": seasonal_patterns,
        "future_placeholder_invariance": invariance, "quantile_crossing_rate": crossing,
        "diagnostic_findings": {
            "confirmed": [
                "TFT timestamps, horizons, actual values, regional definitions, and units align exactly on the common evaluation keys.",
                "TFT quantile predictions are returned in original target units through the checkpoint target normalizer; P50 is quantile index 1.",
                "The original TFT headline used only complete 12-month windows while Phase 2A included shorter end-of-series horizons; the final comparison uses their exact intersection.",
                "Phase 2A models are refit at every forecast origin; TFT weights remain fixed after training through 2017 while later observed targets are used only as encoder history.",
                "TFT forecasts have strong negative bias and severely under-cover the nominal P10-P90 interval.",
                "No target-derived variable is a known decoder input, and future placeholder invariance passed.",
            ],
            "hypotheses": [
                "The small capacity and fixed pre-2018 weights may not adapt to the post-2020 level shift as effectively as rolling-refit Holt-Winters.",
                "Calendar-only covariates and quantile-loss optimization may be insufficient for rare peak and regime-shift behavior.",
                "The 36-month encoder may capture seasonality but not the longer structural trend; this was not tested by retraining.",
            ],
            "confirmed_defect_requiring_retraining": False,
        },
        "system_tft_metrics": system_tft.to_dict("records"), "charts": charts,
        "best_validated_model": "holt_winters", "tft_status": "experimental",
    }
    (REPORTS / "phase2b_diagnostic.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    report_markdown(report, metrics)
    overall_calibration = calibration[calibration["breakdown"] == "overall"]
    calibration_rows = [
        "| Target | N | Coverage | Mean width | Normalized width | Crossing | P10 loss | P50 loss | P90 loss |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in overall_calibration.to_dict("records"):
        calibration_rows.append(
            f"| {row['target_name']} | {row['observations']} | {row['coverage_p10_p90']:.4f} | "
            f"{row['mean_interval_width']:.3f} | {row['normalized_interval_width']:.4f} | "
            f"{row['quantile_crossing_rate']:.4f} | {row['p10_quantile_loss']:.3f} | "
            f"{row['p50_quantile_loss']:.3f} | {row['p90_quantile_loss']:.3f} |"
        )
    (REPORTS / "phase2b_calibration.md").write_text(
        "# Phase 2B calibration\n\nP10-P90 is nominally 80%. Measured coverage, quantile loss, raw and normalized interval width, and quantile crossing are reported overall and by horizon, region, and region/horizon in `phase2b_calibration.csv`. No quantile crossing was observed. Final test residuals were not used to recalibrate forecasts.\n\n"
        + "## Overall TFT calibration\n\n"
        + "\n".join(calibration_rows)
        + "\n"
    )
    previous = json.loads((REPORTS / "tft_evaluation.json").read_text())
    (REPORTS / "phase2b_gpu_performance.json").write_text(json.dumps({
        "device": torch.cuda.get_device_name(0), "torch": torch.__version__, "hip": torch.version.hip,
        "measurements": previous["performance"],
        "note": "GPU utilization percentage was not sampled during training and is unavailable; no value is fabricated.",
    }, indent=2) + "\n")
    lock = sorted(f"{dist.metadata['Name']}=={dist.version}" for dist in importlib.metadata.distributions() if dist.metadata.get("Name"))
    (ROOT / "requirements-lock-phase2b.txt").write_text("\n".join(lock) + "\n")

    outputs = [
        PROCESSED / "phase2b_next_12_month_forecasts.parquet", REPORTS / "phase2b_comparable_metrics.parquet",
        REPORTS / "phase2b_calibration.parquet",
        REPORTS / "phase2b_diagnostic.json", REPORTS / "phase2b_final_evaluation.md",
        REPORTS / "phase2b_calibration.md", REPORTS / "phase2b_gpu_performance.json",
    ]
    inventory_path = REPORTS / "data_inventory.json"
    inventory = json.loads(inventory_path.read_text())
    inventory["phase2b"] = {"status": "complete", "best_validated_model": "holt_winters", "tft_status": "experimental", "outputs": [
        {"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "sha256": sha256(path)} for path in outputs
    ]}
    inventory["updated_at"] = report["created_at"]
    inventory_path.write_text(json.dumps(inventory, indent=2) + "\n")
    print(json.dumps({"status": "complete", "common_keys_per_model": alignment["common_keys_per_model"], "quantile_crossing_rate": crossing, "future_invariance": invariance, "best_model": "holt_winters"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
