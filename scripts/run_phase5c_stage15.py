#!/usr/bin/env python3
"""Build Stage 1.5 skill diagnostics and leakage-safe operational weather features."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from run_phase5c_weather_skill import build_evaluation, load_inputs
from weather.seasonal_skill import add_expanding_normals, chronological_bias_correct, validate_forecast_keys

ROOT = Path(__file__).resolve().parents[1]


def atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".parquet", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary, index=False, compression="zstd")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_all(config: dict[str, object]) -> tuple[pd.DataFrame, pd.DataFrame]:
    forecasts, history = load_inputs(config, False)
    forecasts = forecasts[forecasts.track.eq("archived")].copy()
    validate_forecast_keys(forecasts)
    variables = config["variables"]
    actual = history[["target_month", "weather_zone", *variables]]
    frame = forecasts.merge(actual, on=["target_month", "weather_zone"], how="left", validate="many_to_one")
    for variable in variables:
        frame = frame.rename(columns={variable: f"{variable}_actual",
                                      f"{variable}_mean": f"{variable}_raw"})
    frame = add_expanding_normals(frame, history, variables, int(config["minimum_normal_years"]))
    for variable in variables:
        frame = chronological_bias_correct(
            frame, variable, int(config["minimum_bias_samples"]),
            int(config["era5_publication_lag_days"]),
        )
    return frame, history


def prior_provider(frame: pd.DataFrame, row: pd.Series, variable: str, minimum: int = 12) -> tuple[str | None, pd.Timestamp | None]:
    """Select from archived errors observable before this row's scheduled availability."""
    candidates = frame[
        frame.weather_zone.eq(row.weather_zone)
        & frame.lead_month.eq(row.lead_month)
        & frame[f"{variable}_actual"].notna()
    ].copy()
    observed = (candidates.target_month + pd.offsets.MonthEnd(0) + pd.Timedelta(days=7))
    cutoff = pd.Timestamp(row.available_at)
    if cutoff.tzinfo is not None:
        observed = observed.dt.tz_localize(cutoff.tzinfo)
    candidates = candidates[observed <= cutoff]
    if candidates.empty:
        return None, None
    candidates["absolute_error"] = (
        candidates[f"{variable}_calibrated"] - candidates[f"{variable}_actual"]
    ).abs()
    scores = candidates.groupby("provider", observed=True).agg(
        mae=("absolute_error", "mean"), samples=("absolute_error", "count")
    )
    scores = scores[scores.samples >= minimum]
    if scores.empty:
        return None, None
    return str(scores.mae.idxmin()), pd.Timestamp(observed.loc[candidates.index].max())


def operational_features(frame: pd.DataFrame, history: pd.DataFrame, config: dict[str, object]) -> pd.DataFrame:
    # One template per initialization/zone; issue semantics are common to both providers.
    base = frame[["issue_date", "weather_zone"]].drop_duplicates()
    template = pd.concat([
        base.assign(lead_month=lead,
                    target_month=lambda d, lead=lead: [
                        pd.Timestamp(issue).tz_convert("UTC").tz_localize(None).to_period("M").to_timestamp()
                        + pd.DateOffset(months=lead - 1) for issue in d.issue_date
                    ]) for lead in range(1, 13)
    ], ignore_index=True)
    normal_variables = list(config["variables"])
    template = add_expanding_normals(
        template, history, normal_variables,
        int(config["minimum_normal_years"]),
    )
    records: list[dict[str, object]] = []
    dynamic = frame.set_index(["issue_date", "lead_month", "weather_zone", "provider"])
    for row in template.itertuples(index=False):
        issue = pd.Timestamp(row.issue_date)
        # Climate normal is always a reproducible supported mode.
        for variable in normal_variables:
            records.append({
                "forecast_origin": issue, "weather_initialization": issue,
                "target_month": row.target_month, "forecast_lead": row.lead_month,
                "weather_zone": row.weather_zone, "feature": variable,
                "feature_mode": "expanding_climate_normal",
                "value": getattr(row, f"{variable}_normal"), "provider": "era5_expanding_normal",
                "availability_timestamp": issue, "calibration_training_cutoff": pd.NaT,
                "fallback_reason": "explicit_normal_mode", "availability_evidence": "known_at_origin",
            })
        if row.lead_month > 6:
            records.append({
                "forecast_origin": issue, "weather_initialization": issue,
                "target_month": row.target_month, "forecast_lead": row.lead_month,
                "weather_zone": row.weather_zone, "feature": "temperature_mean_c",
                "feature_mode": "validation_selected_temperature",
                "value": row.temperature_mean_c_normal, "provider": "era5_expanding_normal",
                "availability_timestamp": issue, "calibration_training_cutoff": pd.NaT,
                "fallback_reason": "unsupported_seasonal_lead_7_12",
                "availability_evidence": "known_at_origin",
            })
            continue
        provider_rows: dict[str, pd.Series] = {}
        for provider in ("ncep", "seas5"):
            item = dynamic.loc[(issue, row.lead_month, row.weather_zone, provider)]
            provider_rows[provider] = item
            records.append({
                "forecast_origin": pd.Timestamp(item.available_at), "weather_initialization": issue,
                "target_month": row.target_month, "forecast_lead": row.lead_month,
                "weather_zone": row.weather_zone, "feature": "temperature_mean_c",
                "feature_mode": f"calibrated_{provider}_temperature",
                "value": item.temperature_mean_c_calibrated, "provider": provider,
                "availability_timestamp": item.available_at,
                "calibration_training_cutoff": item.temperature_mean_c_calibration_cutoff,
                "fallback_reason": None,
                "availability_evidence": "scheduled_operational_release_not_file_audit",
            })
        selection_available = max(x.available_at for x in provider_rows.values())
        selector = pd.Series({"weather_zone": row.weather_zone, "lead_month": row.lead_month,
                              "available_at": selection_available})
        selected, selection_cutoff = prior_provider(frame, selector, "temperature_mean_c")
        if selected is None:
            records.append({
                "forecast_origin": issue, "weather_initialization": issue,
                "target_month": row.target_month, "forecast_lead": row.lead_month,
                "weather_zone": row.weather_zone, "feature": "temperature_mean_c",
                "feature_mode": "validation_selected_temperature",
                "value": row.temperature_mean_c_normal, "provider": "era5_expanding_normal",
                "availability_timestamp": issue, "calibration_training_cutoff": pd.NaT,
                "fallback_reason": "insufficient_prior_archived_provider_errors",
                "availability_evidence": "known_at_origin",
            })
        else:
            item = provider_rows[selected]
            records.append({
                "forecast_origin": selection_available, "weather_initialization": issue,
                "target_month": row.target_month, "forecast_lead": row.lead_month,
                "weather_zone": row.weather_zone, "feature": "temperature_mean_c",
                "feature_mode": "validation_selected_temperature",
                "value": item.temperature_mean_c_calibrated, "provider": selected,
                "availability_timestamp": item.available_at,
                "calibration_training_cutoff": selection_cutoff,
                "fallback_reason": None,
                "availability_evidence": "scheduled_operational_release_not_file_audit",
            })
        for variable in ("precipitation_mm", "solar_energy_mj_m2"):
            selected, cutoff = prior_provider(frame, selector, variable)
            if selected is None:
                value, provider, reason, available = (getattr(row, f"{variable}_normal"),
                    "era5_expanding_normal", "insufficient_prior_archived_provider_errors", issue)
            else:
                item = provider_rows[selected]
                value, provider, reason, available = item[f"{variable}_calibrated"], selected, None, selection_available
            records.append({
                "forecast_origin": available, "weather_initialization": issue,
                "target_month": row.target_month, "forecast_lead": row.lead_month,
                "weather_zone": row.weather_zone, "feature": variable,
                "feature_mode": "controlled_ablation_selected",
                "value": value, "provider": provider, "availability_timestamp": available,
                "calibration_training_cutoff": cutoff, "fallback_reason": reason,
                "availability_evidence": ("known_at_origin" if provider == "era5_expanding_normal"
                                          else "scheduled_operational_release_not_file_audit"),
            })
    return pd.DataFrame(records)


def temperature_breakdowns(evaluated: pd.DataFrame) -> pd.DataFrame:
    frame = evaluated.copy()
    frame = frame[frame.temperature_mean_c_actual.notna()]
    month = frame.target_month.dt.month
    frame["season"] = np.select([month.isin([12, 1, 2]), month.isin([3, 4, 5]),
                                  month.isin([6, 7, 8])], ["winter", "spring", "summer"], default="fall")
    magnitude = (frame.temperature_mean_c_actual - frame.temperature_mean_c_normal).abs()
    frame["anomaly_magnitude"] = pd.cut(magnitude, [-np.inf, 1, 2, np.inf],
                                         labels=["under_1c", "1_to_2c", "over_2c"])
    rows = []
    for variant in ("raw", "calibrated"):
        prediction = f"temperature_mean_c_{variant}"
        for keys, group in frame.groupby(
            ["track", "period", "provider", "lead_month", "season", "weather_zone", "anomaly_magnitude"],
            observed=True,
        ):
            error = group[prediction] - group.temperature_mean_c_actual
            rows.append({
                **dict(zip(["track", "period", "provider", "lead_month", "season",
                            "weather_zone", "anomaly_magnitude"], keys, strict=True)),
                "variant": variant, "rows": len(group), "mae": float(error.abs().mean()),
                "rmse": float(np.sqrt(np.mean(np.square(error)))), "bias": float(error.mean()),
            })
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config/phase5c_stage15.json")
    parser.add_argument("--smoke-test", action="store_true")
    options = parser.parse_args()
    config = json.loads(options.config.read_text())
    evaluated, _ = build_evaluation(config, options.smoke_test)
    all_forecasts, history = prepare_all(config)
    if options.smoke_test:
        all_forecasts = all_forecasts[all_forecasts.issue_date <= pd.Timestamp("2022-01-01", tz="UTC")]
    features = operational_features(all_forecasts, history, config)
    breakdowns = temperature_breakdowns(evaluated)
    label = "smoke" if options.smoke_test else "full"
    output = ROOT / "reports/phase5c_stage15" / label
    atomic_parquet(features, output / "operational_weather_features.parquet")
    atomic_parquet(breakdowns, output / "temperature_skill_breakdowns.parquet")
    audit = {
        "status": "smoke_passed" if options.smoke_test else "complete",
        "operational_feature_rows": len(features), "temperature_breakdown_rows": len(breakdowns),
        "nonfinite_values": int((~np.isfinite(features.value.dropna())).sum()),
        "missing_values": int(features.value.isna().sum()),
        "future_calibration_cutoff_violations": int((
            pd.to_datetime(features.calibration_training_cutoff, utc=True)
            > pd.to_datetime(features.forecast_origin, utc=True)
        ).sum()),
        "availability_after_origin_violations": int((
            pd.to_datetime(features.availability_timestamp, utc=True)
            > pd.to_datetime(features.forecast_origin, utc=True)
        ).sum()),
        "target_alignment_violations": int((
            pd.to_datetime(features.weather_initialization, utc=True).dt.tz_localize(None).dt.to_period("M")
            + features.forecast_lead.astype(int).sub(1).to_numpy()
            != pd.to_datetime(features.target_month).dt.to_period("M")
        ).sum()),
        "dynamic_unsupported_lead_violations": int((
            features.provider.isin(["ncep", "seas5"]) & features.forecast_lead.gt(6)
        ).sum()),
        "daily_features_fabricated": False,
        "availability_evidence": "scheduled provider release, not file-specific archive audit",
        "stage2_started": False,
        "outputs": {"features": str(output / "operational_weather_features.parquet"),
                    "temperature_breakdowns": str(output / "temperature_skill_breakdowns.parquet")},
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "readiness_audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    print(json.dumps(audit, indent=2))
    violations = sum(audit[key] for key in (
        "future_calibration_cutoff_violations", "availability_after_origin_violations",
        "target_alignment_violations", "dynamic_unsupported_lead_violations", "nonfinite_values",
    ))
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
