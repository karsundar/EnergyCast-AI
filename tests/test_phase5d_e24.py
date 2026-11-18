from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.forecasting.e24 import reconstruct

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/processed/phase5d/e24/smoke"
REPORT = ROOT / "reports/phase5d/e24/smoke"


def test_e24_multiplicative_reconstruction_and_zero_shrinkage():
    reference = np.array([100.0, 200.0])
    ratio = np.log(np.array([1.1, 0.9]))
    assert np.allclose(reconstruct(reference, ratio, 1.0), [110.0, 180.0])
    assert np.allclose(reconstruct(reference, ratio, 0.0), reference)


def test_e24_smoke_is_positive_non_oracle_and_leakage_safe():
    frame = pd.read_parquet(OUTPUT / "E24_predictions.parquet")
    assert set(frame.experiment) == {"E24-A", "E24-B", "E24-C"}
    assert not frame.oracle_weather.any()
    assert (frame[["reference_value", "p50"]] > 0).all().all()
    assert (pd.to_datetime(frame.reference_month) <= pd.to_datetime(frame.origin)).all()
    assert (pd.to_datetime(frame.label_training_cutoff, utc=True)
            <= pd.to_datetime(frame.operational_forecast_time, utc=True)).all()
    assert (pd.to_datetime(frame.weather_available_at, utc=True)
            <= pd.to_datetime(frame.operational_forecast_time, utc=True)).all()


def test_e24_variants_have_controlled_references_and_shrinkage():
    frame = pd.read_parquet(OUTPUT / "E24_predictions.parquet")
    assert set(frame.loc[frame.experiment.eq("E24-A"), "reference_type"]) == {"seasonal_naive"}
    assert set(frame.loc[frame.experiment.eq("E24-C"), "reference_type"]) == {"seasonal_naive"}
    assert set(frame.loc[frame.experiment.eq("E24-B"), "reference_type"]) == {"holt_winters_oos"}
    assert frame.loc[frame.experiment.eq("E24-A"), "shrinkage_lambda"].eq(1).all()
    report = json.loads((REPORT / "E24_report.json").read_text())
    assert report["positive_reference_violations"] == 0
    assert report["reference_after_origin_violations"] == 0
    assert report["future_label_cutoff_violations"] == 0
    selection = {(row["target_name"], row["reference_type"]): row for row in report["lambda_selection"]}
    assert len(selection) == 4
    assert all("0.0" in row["development_wape"] for row in selection.values())


def test_e24_common_population_and_paired_uncertainty_are_exact():
    common = pd.read_parquet(OUTPUT / "E24_common_predictions.parquet")
    expected = {"E24-A", "E24-B", "E24-C", "E0", "E9", "E14", "E25-A", "E25-B", "E25-C"}
    assert set(common.experiment) == expected
    keys = ["target_name", "origin", "target_month", "horizon", "geography"]
    assert common.groupby(keys, observed=True).experiment.nunique().eq(len(expected)).all()
    paired = pd.read_csv(REPORT / "E24_paired_differences.csv")
    assert set(paired.candidate) == {"E24-A", "E24-B", "E24-C"}
    assert set(paired.reference) == {"E0", "E9", "E14", "E25-A", "E25-B", "E25-C"}
    assert paired[["wape_difference_ci_low", "wape_difference_ci_high"]].notna().all().all()


def test_e24_issuance_schedules_are_not_conflated():
    audit = pd.read_csv(REPORT / "E24_issuance_audit.csv")
    assert set(audit.issuance_schedule) == {"month_start", "day_15_refresh"}
    assert audit.loc[audit.issuance_schedule.eq("month_start"), "evaluated"].iloc[0]
    assert not audit.loc[audit.issuance_schedule.eq("day_15_refresh"), "evaluated"].iloc[0]
