from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/phase5d/diagnostics"


def test_diagnostics_preserve_saved_experiments_and_find_range_shift():
    frame = pd.read_parquet(REPORT / "enriched_predictions.parquet")
    assert set(frame.experiment) == {"E18", "E19", "E20"}
    system = frame[frame.geography.eq("ERCOT_SYSTEM")]
    assert system.above_training_max.any()
    assert not system.prediction_above_training_max.any()
    assert system.loc[system.above_training_max, "error"].lt(0).all()


def test_provider_fallback_paired_differences_are_exactly_zero():
    paired = pd.read_parquet(REPORT / "paired_error_differences.parquet")
    fallback = paired[paired.horizon_band.eq("7-12")]
    assert fallback.wape_difference.eq(0).all()
    assert fallback.mean_absolute_error_difference.eq(0).all()


def test_operational_issuance_requires_normals_at_month_start():
    audit = pd.read_parquet(REPORT / "issuance_availability_audit.parquet")
    start = audit[audit.issuance_schedule.eq("month_start")]
    refresh = audit[audit.issuance_schedule.eq("mid_month_refresh")]
    assert not start.seasonal_weather_available.any()
    assert refresh.seasonal_weather_available.all()


def test_experiment_design_starts_no_training():
    summary = json.loads((REPORT / "diagnostic_summary.json").read_text())
    design = json.loads((REPORT / "experiment_design.json").read_text())
    assert summary["experiments_started"] == []
    assert design["status"] == "design_only_no_training"
    assert set(summary["experiments_designed"]) == {"E24", "E25", "E26", "E27"}
