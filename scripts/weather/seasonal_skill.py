"""Leakage-safe seasonal-weather verification and chronological calibration."""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

KEYS = ["provider", "track", "issue_date", "target_month", "lead_month", "weather_zone"]


def validate_forecast_keys(frame: pd.DataFrame) -> None:
    """Reject inconsistent targets, duplicate keys, or fabricated hindcast availability."""
    if frame.duplicated(KEYS).any():
        raise ValueError("duplicate seasonal forecast keys")
    issue_month = pd.to_datetime(frame.issue_date, utc=True).dt.tz_localize(None).dt.to_period("M")
    expected = issue_month + frame.lead_month.astype(int).sub(1).to_numpy()
    actual = pd.to_datetime(frame.target_month).dt.to_period("M")
    if not (expected == actual).all():
        raise ValueError("target month is inconsistent with issue month and lead")
    archived = frame.track.eq("archived")
    if frame.loc[archived, "available_at"].isna().any():
        raise ValueError("archived forecast lacks verified availability")
    if frame.loc[~archived, "available_at"].notna().any():
        raise ValueError("retrospective hindcast has synthetic availability")
    if (pd.to_datetime(frame.loc[archived, "available_at"], utc=True)
            < pd.to_datetime(frame.loc[archived, "issue_date"], utc=True)).any():
        raise ValueError("forecast availability precedes initialization")


def add_expanding_normals(
    forecasts: pd.DataFrame, history: pd.DataFrame, variables: Iterable[str], minimum_years: int,
) -> pd.DataFrame:
    """Add same-calendar-month ERA5 means using observations before initialization only."""
    result = forecasts.copy()
    history = history.copy()
    history["target_month"] = pd.to_datetime(history.target_month).dt.to_period("M").dt.to_timestamp()
    for variable in variables:
        values: list[float] = []
        counts: list[int] = []
        for row in result.itertuples(index=False):
            issue = pd.Timestamp(row.issue_date).tz_localize(None).to_period("M").to_timestamp()
            target = pd.Timestamp(row.target_month)
            eligible = history[
                history.weather_zone.eq(row.weather_zone)
                & history.target_month.lt(issue)
                & history.target_month.dt.month.eq(target.month)
            ]
            count = int(eligible.target_month.dt.year.nunique())
            counts.append(count)
            values.append(float(eligible[variable].mean()) if count >= minimum_years else np.nan)
        result[f"{variable}_normal"] = values
        result[f"{variable}_normal_years"] = counts
    return result


def chronological_bias_correct(
    frame: pd.DataFrame, variable: str, minimum_samples: int, publication_lag_days: int,
) -> pd.DataFrame:
    """Subtract mean prior error only after its verifying observation was available."""
    result = frame.sort_values(["issue_date", "provider", "lead_month", "weather_zone"]).copy()
    result[f"{variable}_bias_correction"] = np.nan
    result[f"{variable}_calibration_samples"] = 0
    result[f"{variable}_calibration_cutoff"] = pd.Series(
        pd.NaT, index=result.index, dtype="datetime64[ns, UTC]"
    )
    grouping = ["provider", "track", "lead_month", "weather_zone"]
    for _, indexes in result.groupby(grouping, observed=True).groups.items():
        prior: list[tuple[pd.Timestamp, float]] = []
        for index in indexes:
            row = result.loc[index]
            cutoff = (pd.Timestamp(row.available_at) if row.track == "archived"
                      else pd.Timestamp(row.issue_date))
            eligible = [error for observed_at, error in prior if observed_at <= cutoff]
            eligible_dates = [observed_at for observed_at, _ in prior if observed_at <= cutoff]
            result.at[index, f"{variable}_calibration_samples"] = len(eligible)
            if eligible_dates:
                result.at[index, f"{variable}_calibration_cutoff"] = max(eligible_dates).tz_convert("UTC")
            if len(eligible) >= minimum_samples:
                result.at[index, f"{variable}_bias_correction"] = float(np.mean(eligible))
            if pd.notna(row[f"{variable}_actual"]) and pd.notna(row[f"{variable}_raw"]):
                observed_at = (pd.Timestamp(row.target_month) + pd.offsets.MonthEnd(0)
                               + pd.Timedelta(days=publication_lag_days))
                if cutoff.tzinfo is not None:
                    observed_at = observed_at.tz_localize(cutoff.tzinfo)
                prior.append((observed_at, float(row[f"{variable}_raw"] - row[f"{variable}_actual"])))
    correction = result[f"{variable}_bias_correction"].fillna(0.0)
    for statistic in ("raw", "p10", "p50", "p90"):
        source = f"{variable}_{statistic}"
        if source in result:
            destination = (f"{variable}_calibrated" if statistic == "raw"
                           else f"{variable}_{statistic}_calibrated")
            result[destination] = result[source] - correction
    return result


def metric_rows(frame: pd.DataFrame, variable: str, variant: str) -> list[dict[str, object]]:
    prediction = f"{variable}_{variant}"
    rows: list[dict[str, object]] = []
    dimensions = ["track", "period", "provider", "lead_month", "geography_scope"]
    working = frame.copy()
    working["geography_scope"] = np.where(working.weather_zone.eq("SYSTEM"), "system", "regional")
    for key, group in working.groupby(dimensions, observed=True, dropna=False):
        valid = group.dropna(subset=[prediction, f"{variable}_actual"])
        if valid.empty:
            continue
        error = valid[prediction] - valid[f"{variable}_actual"]
        normal = valid[f"{variable}_normal"]
        forecast_anomaly = valid[prediction] - normal
        actual_anomaly = valid[f"{variable}_actual"] - normal
        correlation = (forecast_anomaly.corr(actual_anomaly)
                       if len(valid) >= 3 and forecast_anomaly.std() > 0 and actual_anomaly.std() > 0
                       else np.nan)
        p10 = f"{variable}_p10{'_calibrated' if variant == 'calibrated' else ''}"
        p90 = f"{variable}_p90{'_calibrated' if variant == 'calibrated' else ''}"
        interval = (valid.dropna(subset=[p10, p90])
                    if variant in {"raw", "calibrated"} else valid.iloc[0:0])
        rows.append({
            **dict(zip(dimensions, key, strict=True)), "variable": variable, "variant": variant,
            "rows": len(valid), "mae": float(error.abs().mean()),
            "rmse": float(math.sqrt(np.mean(np.square(error)))), "bias": float(error.mean()),
            "anomaly_correlation": float(correlation) if pd.notna(correlation) else np.nan,
            "p10_p90_coverage": (float(((interval[f"{variable}_actual"] >= interval[p10])
                                         & (interval[f"{variable}_actual"] <= interval[p90])).mean())
                                   if len(interval) else np.nan),
            "mean_interval_width": (float((interval[p90] - interval[p10]).mean())
                                    if len(interval) else np.nan),
        })
    return rows


def validation_weights(metrics: pd.DataFrame) -> pd.DataFrame:
    """Inverse-MAE provider weights derived exclusively from the validation period."""
    source = metrics[
        metrics.period.isin(["validation", "hindcast_research_validation"])
        & metrics.variant.eq("calibrated")
    ].copy()
    source = source[source.provider.isin(["seas5", "ncep"])]
    source["inverse_mae"] = 1 / source.mae.clip(lower=1e-12)
    keys = ["track", "variable", "lead_month", "geography_scope"]
    source["weight"] = source.inverse_mae / source.groupby(keys, observed=True).inverse_mae.transform("sum")
    return source[keys + ["provider", "weight", "rows", "mae"]]


def weighted_provider_ensemble(
    frame: pd.DataFrame, weights: pd.DataFrame, variables: Iterable[str],
) -> pd.DataFrame:
    """Combine calibrated providers using validation-only inverse-MAE weights."""
    records: list[pd.DataFrame] = []
    base_keys = ["track", "issue_date", "target_month", "lead_month", "weather_zone", "period"]
    working = frame.copy()
    working["geography_scope"] = np.where(working.weather_zone.eq("SYSTEM"), "system", "regional")
    for variable in variables:
        selected = weights[weights.variable.eq(variable)]
        merged = working.merge(
            selected[["track", "lead_month", "geography_scope", "provider", "weight"]],
            on=["track", "lead_month", "geography_scope", "provider"], how="inner",
            validate="many_to_one",
        )
        if merged.empty:
            continue
        value_columns = [
            f"{variable}_calibrated", f"{variable}_p10_calibrated",
            f"{variable}_p50_calibrated", f"{variable}_p90_calibrated",
        ]
        for column in value_columns:
            merged[f"weighted_{column}"] = merged[column] * merged.weight
        aggregated = merged.groupby(base_keys, observed=True, as_index=False).agg(
            **{column: (f"weighted_{column}", "sum") for column in value_columns},
            provider_weight_sum=("weight", "sum"), provider_count=("provider", "nunique"),
        )
        actual_columns = [f"{variable}_actual", f"{variable}_normal"]
        reference = working[base_keys + actual_columns].drop_duplicates(base_keys)
        aggregated = aggregated.merge(reference, on=base_keys, validate="one_to_one")
        aggregated["variable"] = variable
        records.append(aggregated)
    if not records:
        return pd.DataFrame()
    return pd.concat(records, ignore_index=True)
