from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/processed/phase5d/e25/smoke"
REPORT = ROOT / "reports/phase5d/e25/smoke"


def test_e25_residual_labels_are_historical_oos_and_published_after_target():
    cache = pd.read_parquet(ROOT / "data/processed/phase4b/residual_cache_holt_winters_total_energy_mwh.parquet")
    assert (cache.target_month > cache.origin).all()
    available = pd.to_datetime(cache.target_month) + pd.offsets.MonthEnd(0) + pd.Timedelta(days=1)
    assert (available > cache.target_month).all()


def test_e25_smoke_is_non_oracle_and_issuance_safe():
    frame = pd.read_parquet(OUTPUT / "E25_predictions.parquet")
    assert set(frame.experiment) == {"E25-A", "E25-B", "E25-C"}
    assert not frame.oracle_weather.any()
    assert (pd.to_datetime(frame.residual_training_cutoff, utc=True)
            <= pd.to_datetime(frame.operational_forecast_time, utc=True)).all()
    assert (pd.to_datetime(frame.weather_available_at, utc=True)
            <= pd.to_datetime(frame.operational_forecast_time, utc=True)).all()
    assert set(frame.loc[frame.experiment.eq("E25-A"), "issuance_schedule"]) == {"month_start"}
    assert set(frame.loc[frame.experiment.ne("E25-A"), "issuance_schedule"]) == {"mid_month_refresh"}


def test_e25_lambda_is_validation_selected_and_shared_by_weather_variants():
    report = json.loads((REPORT / "E25_report.json").read_text())
    selection = {row["target_name"]: row for row in report["lambda_selection"]}
    assert set(selection) == {"total_energy_mwh", "peak_demand_mw"}
    for target, row in selection.items():
        assert "0.0" in row["development_wape"]
        assert 0 <= row["selected_lambda"] <= 1
        frame = pd.read_parquet(OUTPUT / "E25_predictions.parquet")
        assert frame.loc[frame.target_name.eq(target), "shrinkage_lambda"].nunique() == 1


def test_e25_common_population_contains_controls_and_uncertainty():
    common = pd.read_parquet(OUTPUT / "E25_common_predictions.parquet")
    assert set(common.experiment) == {"E0", "E9", "E25-A", "E25-B", "E25-C"}
    keys = ["target_name", "origin", "target_month", "horizon", "geography"]
    assert common.groupby(keys, observed=True).experiment.nunique().eq(5).all()
    paired = pd.read_csv(REPORT / "E25_paired_differences.csv")
    assert set(paired.reference) == {"E0", "E9"}
    assert paired[["wape_difference_ci_low", "wape_difference_ci_high"]].notna().all().all()
