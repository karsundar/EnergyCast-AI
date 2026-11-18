from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.forecasting.hybrid import (
    apply_residual_model, assert_residual_samples_leakage_safe, fit_residual_model,
    residual_columns, select_residual_transform, validation_residual_calibration,
)
from scripts.forecasting.calibration import (
    apply_calibration, apply_interval_inflation, fit_calibration,
    fit_interval_inflation, select_method,
)


def synthetic_residuals() -> pd.DataFrame:
    origins = pd.date_range("2014-01-01", "2020-12-01", freq="MS")
    rows = []
    for index, origin in enumerate(origins):
        baseline = 100.0 + index
        actual = baseline * 1.05
        rows.append({
            "origin": origin, "target_month": origin + pd.DateOffset(months=1),
            "geography": "ERCOT_SYSTEM", "actual": actual,
            "baseline_prediction": baseline, "level_residual": actual - baseline,
            "growth_residual": 0.05, "geography_code": 8.0, "horizon": 1.0,
            "baseline": baseline, "origin_value": baseline - 1,
            "baseline_growth": 0.01, "origin_mom_growth": 0.01,
            "origin_yoy_growth": 0.1, "month_number": float((index % 12) + 1),
            "quarter": float((index % 12) // 3 + 1), "month_sin": 0.0,
            "month_cos": 1.0, "days_in_month": 30.0,
        })
    return pd.DataFrame(rows)


def test_residual_targets_are_out_of_sample_metadata() -> None:
    samples = synthetic_residuals()
    assert {"origin", "target_month", "baseline_prediction", "actual"}.issubset(samples)
    assert (samples["target_month"] > samples["origin"]).all()
    assert not {"actual", "level_residual", "growth_residual"} & set(residual_columns(samples))
    assert_residual_samples_leakage_safe(samples)


def test_residual_leakage_guard_rejects_same_origin_target() -> None:
    samples = synthetic_residuals()
    samples.loc[samples.index[0], "target_month"] = samples.loc[samples.index[0], "origin"]
    try:
        assert_residual_samples_leakage_safe(samples)
    except ValueError as error:
        assert "strictly after" in str(error)
    else:
        raise AssertionError("leaking residual sample was accepted")


def test_growth_transform_selection_and_application() -> None:
    samples = synthetic_residuals()
    params = {"n_estimators": 20, "learning_rate": 0.05, "num_leaves": 7, "random_state": 42, "n_jobs": 1}
    transform, scores = select_residual_transform(samples, params)
    assert transform in {"level", "growth"}
    assert all(np.isfinite(list(scores.values())))
    model = fit_residual_model(samples, transform, params)
    query = samples.tail(2)[residual_columns(samples)]
    values = apply_residual_model(model, query, samples.tail(2)["baseline_prediction"].to_numpy(), transform)
    assert np.isfinite(values).all()


def test_intervals_use_development_residuals() -> None:
    samples = synthetic_residuals()
    params = {"n_estimators": 20, "learning_rate": 0.05, "num_leaves": 7, "random_state": 42, "n_jobs": 1}
    offsets = validation_residual_calibration(samples, params, "growth")
    assert 1 in offsets
    assert np.isfinite(offsets[1]).all()


def synthetic_calibration() -> pd.DataFrame:
    rows = []
    for geography, scale in (("COAST", 100.0), ("ERCOT_SYSTEM", 1000.0)):
        for month in pd.date_range("2018-01-01", "2020-12-01", freq="MS"):
            prediction = scale * (1 + month.month / 100)
            rows.append({"target_name": "total_energy_mwh", "geography": geography,
                         "horizon": month.month, "target_month": month,
                         "p50": prediction, "actual": prediction * 1.05})
    return pd.DataFrame(rows)


def test_scale_aware_calibration_does_not_pool_absolute_regions() -> None:
    frame = synthetic_calibration()
    fitted = fit_calibration(frame, "scale_normalized", min_group_samples=2)
    assert set(fitted.scales.index.get_level_values("geography")) == {"COAST", "ERCOT_SYSTEM"}
    calibrated = apply_calibration(frame.tail(24), fitted)
    assert (calibrated["p10"] <= calibrated["p50"]).all()
    assert (calibrated["p50"] <= calibrated["p90"]).all()


def test_calibration_method_selection_uses_2020_holdout() -> None:
    selected, scores = select_method(synthetic_calibration(), min_group_samples=2, objective="pinball")
    assert selected in {"relative", "scale_normalized"}
    assert set(scores) == {"relative", "scale_normalized"}


def test_interval_inflation_is_geography_specific() -> None:
    frame = synthetic_calibration()
    calibrated = apply_calibration(frame, fit_calibration(frame, "scale_normalized", 2))
    factors = fit_interval_inflation(calibrated, .8)
    assert len(factors) == 2
    inflated = apply_interval_inflation(calibrated, factors)
    assert inflated["actual"].between(inflated["p10"], inflated["p90"]).mean() >= .8
