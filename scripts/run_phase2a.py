#!/usr/bin/env python3
"""Run Phase 2A ERCOT feature engineering and statistical baselines."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from forecasting.backtest import (BacktestConfig, build_direct_samples, evaluate_predictions,
                                  rolling_predictions, tune_lightgbm, add_intervals)
from forecasting.features import FeatureConfig, write_feature_outputs


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
CHARTS = REPORTS / "charts"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--completeness-threshold", type=float, default=99.0)
    parser.add_argument("--refresh-inventory-only", action="store_true")
    return parser.parse_args()


def refresh_inventory(report: dict[str, Any], report_md: Path) -> None:
    inventory_path = REPORTS / "data_inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["updated_at"] = report["created_at"]
    inventory["phase2a"] = {
        "scope": "ERCOT feature engineering and statistical baselines",
        "outputs": report["outputs"],
        "predictions": "data/processed/baseline_predictions.parquet",
        "metrics": "reports/baseline_metrics.parquet",
        "evaluation_report": str(report_md.relative_to(ROOT)),
        "test_period": report["test_period"],
        "prediction_status_counts": report["prediction_status_counts"],
    }
    inventory_path.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")


def output_metadata(paths: list[Path]) -> list[dict[str, Any]]:
    result = []
    for path in paths:
        frame = pd.read_parquet(path) if path.suffix == ".parquet" else None
        result.append({
            "path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "sha256": sha256(path),
            "rows": len(frame) if frame is not None else None,
        })
    return result


def create_charts(predictions: pd.DataFrame, metrics: pd.DataFrame, targets: pd.DataFrame) -> list[Path]:
    CHARTS.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    system = metrics[(metrics["breakdown"] == "system_horizon") & (metrics["target_name"] == "total_energy_mwh")]
    fig, axis = plt.subplots(figsize=(9, 5))
    for model, frame in system.groupby("model"):
        axis.plot(frame["horizon"], frame["wape"], marker="o", label=model)
    axis.set(title="ERCOT system monthly energy WAPE by horizon", xlabel="Forecast horizon (months)", ylabel="WAPE (%)")
    axis.grid(alpha=0.25); axis.legend(); fig.tight_layout()
    path = CHARTS / "phase2a_system_wape_by_horizon.png"; fig.savefig(path, dpi=140); plt.close(fig); paths.append(path)

    first_horizon = predictions[
        (predictions["geography"] == "ERCOT_SYSTEM") & (predictions["target_name"] == "total_energy_mwh")
        & (predictions["horizon"] == 1) & (predictions["forecast_status"] == "complete")
    ]
    fig, axis = plt.subplots(figsize=(11, 5))
    actual = first_horizon.drop_duplicates("target_month").sort_values("target_month")
    axis.plot(actual["target_month"], actual["actual"], color="black", linewidth=2, label="actual")
    for model, frame in first_horizon.groupby("model"):
        frame = frame.sort_values("target_month")
        axis.plot(frame["target_month"], frame["p50"], alpha=0.8, label=model)
    axis.set(title="ERCOT system one-month-ahead energy forecasts", xlabel="Target month", ylabel="MWh")
    axis.grid(alpha=0.25); axis.legend(ncol=2); fig.tight_layout()
    path = CHARTS / "phase2a_system_h1_forecasts.png"; fig.savefig(path, dpi=140); plt.close(fig); paths.append(path)

    coverage = metrics[(metrics["breakdown"] == "system_horizon") & (metrics["target_name"] == "total_energy_mwh")]
    fig, axis = plt.subplots(figsize=(9, 5))
    for model, frame in coverage.groupby("model"):
        axis.plot(frame["horizon"], frame["interval_coverage"] * 100, marker="o", label=model)
    axis.axhline(80, color="black", linestyle="--", linewidth=1, label="nominal 80%")
    axis.set(title="Empirical P10–P90 interval coverage", xlabel="Forecast horizon (months)", ylabel="Coverage (%)")
    axis.grid(alpha=0.25); axis.legend(); fig.tight_layout()
    path = CHARTS / "phase2a_interval_coverage.png"; fig.savefig(path, dpi=140); plt.close(fig); paths.append(path)
    return paths


def main() -> int:
    args = parse_args()
    report_json = REPORTS / "baseline_evaluation.json"
    report_md = REPORTS / "baseline_evaluation.md"
    if args.refresh_inventory_only:
        refresh_inventory(json.loads(report_json.read_text(encoding="utf-8")), report_md)
        print(f"inventory: {REPORTS / 'data_inventory.json'}")
        return 0
    if not 0 < args.completeness_threshold <= 100:
        raise ValueError("completeness threshold must be in (0, 100]")
    REPORTS.mkdir(parents=True, exist_ok=True)
    feature_config = FeatureConfig(completeness_threshold_pct=args.completeness_threshold)
    targets, features = write_feature_outputs(PROCESSED, feature_config)
    backtest_config = BacktestConfig()

    tuning: dict[str, Any] = {}
    params: dict[str, dict[str, Any]] = {}
    samples: dict[str, pd.DataFrame] = {}
    for target_name in ("total_energy_mwh", "peak_demand_mw"):
        samples[target_name] = build_direct_samples(targets, features, target_name, backtest_config.max_horizon)
        params[target_name], tuning[target_name] = tune_lightgbm(samples[target_name], backtest_config)

    calibration_origins = pd.date_range("2019-01-01", "2020-11-01", freq="MS")
    test_origins = pd.date_range("2020-12-01", targets["month"].max(), freq="MS")
    calibration_frames: list[pd.DataFrame] = []
    test_frames: list[pd.DataFrame] = []
    for target_name in ("total_energy_mwh", "peak_demand_mw"):
        calibration = rolling_predictions(
            targets, features, target_name, calibration_origins, params[target_name], backtest_config
        )
        calibration = calibration[calibration["target_month"].between(pd.Timestamp("2020-01-01"), pd.Timestamp("2020-12-01"))]
        calibration_frames.append(calibration)
        test_frames.append(rolling_predictions(
            targets, features, target_name, test_origins, params[target_name], backtest_config
        ))
    calibration = pd.concat(calibration_frames, ignore_index=True)
    predictions = add_intervals(pd.concat(test_frames, ignore_index=True), calibration)
    metrics = evaluate_predictions(predictions, targets)

    predictions_path = PROCESSED / "baseline_predictions.parquet"
    metrics_parquet = REPORTS / "baseline_metrics.parquet"
    metrics_csv = REPORTS / "baseline_metrics.csv"
    tuning_path = REPORTS / "lightgbm_tuning.json"
    predictions.to_parquet(predictions_path, index=False, compression="zstd")
    metrics.to_parquet(metrics_parquet, index=False, compression="zstd")
    metrics.to_csv(metrics_csv, index=False)
    tuning_path.write_text(json.dumps({"development_period": "2018-01 through 2019-12", "results": tuning, "selected": params}, indent=2) + "\n")
    chart_paths = create_charts(predictions, metrics, targets)

    overall = metrics[(metrics["breakdown"] == "period_category") & (metrics["period_category"] == "all_test")]
    summary_rows = overall.sort_values(["target_name", "wape"])[
        ["target_name", "model", "observations", "mae", "rmse", "mape", "wape", "mase", "forecast_bias",
         "directional_accuracy", "interval_coverage"]
    ].to_dict("records")
    generated_paths = [
        PROCESSED / "ercot_monthly_targets.parquet", PROCESSED / "ercot_monthly_features.parquet",
        PROCESSED / "economic_monthly_features.parquet", PROCESSED / "geopolitical_monthly_features.parquet",
        predictions_path, metrics_parquet,
    ]
    incomplete = predictions["forecast_status"].value_counts().to_dict()
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "phase": "2A", "completeness_threshold_pct": args.completeness_threshold,
        "training_period": "2004-01 through 2017-12", "tuning_period": "2018-01 through 2019-12",
        "interval_calibration_period": "2020-01 through 2020-12", "test_period": "2021-01 through latest actual",
        "selected_lightgbm_parameters": params, "prediction_status_counts": incomplete,
        "target_quality_flags": targets["quality_flag"].value_counts().to_dict(),
        "system_summary": summary_rows, "outputs": output_metadata(generated_paths),
        "charts": [str(path.relative_to(ROOT)) for path in chart_paths],
        "constraints": {
            "intermediate_future_actuals_used": False, "economic_or_geopolitical_baseline_inputs": False,
            "copernicus_downloaded": False, "tft_trained": False,
            "extreme_weather_breakdown": "pre-test calendar-month 95th-percentile peak-load proxy; no climate data used",
        },
    }
    report_json.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    refresh_inventory(report, report_md)

    lines = [
        "# Phase 2A baseline evaluation", "", f"Generated: {report['created_at']}", "",
        "## Scope and leakage controls", "",
        "- ERCOT monthly energy and peak targets cover all eight weather zones plus the simultaneous regional system sum.",
        f"- Periods below {args.completeness_threshold:.1f}% completeness are excluded; incomplete observed periods remain explicitly flagged.",
        "- Calendar features are known-future inputs. Demand lags, trailing statistics, growth, and peak features use only data available at each forecast origin.",
        "- All models produce 12-month paths without consuming actual intermediate future demand.",
        "- LightGBM candidates were selected on 2018–2019 only. Empirical intervals use 2020 residuals only. Test results begin in 2021.",
        "- Economic and geopolitical feature files are prepared separately and excluded from all baseline fits.", "",
        "## ERCOT system test summary", "",
        "| Target | Model | N | MAE | RMSE | WAPE % | MASE | Bias | Direction accuracy | P10–P90 coverage |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        lines.append(
            f"| {row['target_name']} | {row['model']} | {int(row['observations'])} | {row['mae']:.3f} | "
            f"{row['rmse']:.3f} | {row['wape']:.3f} | {row['mase']:.3f} | {row['forecast_bias']:.3f} | "
            f"{row['directional_accuracy']:.3f} | {row['interval_coverage']:.3f} |"
        )
    lines.extend([
        "", "## Data-quality and evaluation notes", "",
        f"- Prediction statuses: {incomplete}.",
        "- The 2016-11 missing interval remains in the targets and is flagged according to the configurable completeness threshold; no observation is synthesized.",
        "- The source 2021 ERCOT-total discrepancy is preserved. System targets are always built from simultaneous regional sums.",
        "- COVID breakdown covers the portion of March 2020–June 2021 present in the test set. Extreme-period results use a load-based proxy because Copernicus remains disabled.",
        "- Full zone/horizon/season, COVID, extreme-load, interval, quantile-loss, and incomplete-horizon results are in the metrics and prediction artifacts.",
    ])
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"predictions: {predictions_path}")
    print(f"metrics: {metrics_csv}")
    print(f"report: {report_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
