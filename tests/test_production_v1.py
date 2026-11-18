from __future__ import annotations

import json
from pathlib import Path
import unittest
import hashlib
import tempfile

import pandas as pd

from scripts.forecasting.production import build_frozen_forecast, forecast_id, validate_forecast
from scripts.forecasting.frozen_bundle import validate_bundle


def config() -> dict:
    return {
        "production_version": "test-v1", "forecast_horizon_months": 12,
        "models": {
            "total_energy_mwh": {"experiment": "E14", "weights": {"E0": .25, "E8": .25, "E9": .25, "E11": .25}, "backtest_wape_pct": 4.409},
            "peak_demand_mw": {"experiment": "E24-B", "backtest_wape_pct": 6.201},
        },
    }


def bundle() -> pd.DataFrame:
    rows = []
    for target, experiments in (("total_energy_mwh", ("E0", "E8", "E9", "E11")), ("peak_demand_mw", ("E24-B",))):
        for experiment in experiments:
            for horizon in range(1, 13):
                rows.append({"target_name": target, "origin": "2025-04-01", "target_month": pd.Timestamp("2025-04-01") + pd.DateOffset(months=horizon), "horizon": horizon, "geography": "ERCOT_SYSTEM", "experiment": experiment, "p50": 100 + horizon})
    return pd.DataFrame(rows)


class ProductionV1Tests(unittest.TestCase):
    def test_frozen_forecast_is_deterministic_and_has_no_intervals(self):
        first = build_frozen_forecast(bundle(), config(), pd.Timestamp("2025-05-01", tz="UTC"), pd.Timestamp("2025-04-01"), "vintage")
        second = build_frozen_forecast(bundle(), config(), pd.Timestamp("2025-05-01", tz="UTC"), pd.Timestamp("2025-04-01"), "vintage")
        pd.testing.assert_frame_equal(first, second)
        self.assertFalse({"p10", "p90"} & set(first))
        validate_forecast(first, 12, {"ERCOT_SYSTEM"})
        self.assertEqual(forecast_id(first, "abc"), forecast_id(second, "abc"))

    def test_incomplete_ensemble_is_rejected(self):
        incomplete = bundle().query("experiment != 'E11'")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            build_frozen_forecast(incomplete, config(), pd.Timestamp("2025-05-01", tz="UTC"), pd.Timestamp("2025-04-01"), "vintage")

    def test_invalid_horizon_is_rejected(self):
        forecast = build_frozen_forecast(bundle(), config(), pd.Timestamp("2025-05-01", tz="UTC"), pd.Timestamp("2025-04-01"), "vintage")
        with self.assertRaisesRegex(ValueError, "horizons"):
            validate_forecast(forecast[forecast.horizon.ne(12)], 12, {"ERCOT_SYSTEM"})

    def test_bundle_checksum_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            required = [
                "statistical_models.joblib", "e9_energy_residual_lightgbm.txt",
                "e24b_peak_log_ratio_lightgbm.txt", "training_context.parquet",
                "reference_forecasts.parquet", "e9_inference_features.parquet",
                "e24b_inference_features.parquet", "feature_schema.json",
                "chronos2/model.safetensors", "chronos2/config.json",
            ]
            artifacts = {}
            for relative in required:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                payload = b"{}" if relative == "feature_schema.json" else b"test"
                path.write_bytes(payload)
                artifacts[relative] = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
            (root / "bundle_manifest.json").write_text(json.dumps({
                "artifacts": artifacts, "feature_schema": {}, "public_probabilistic_intervals": False,
                "forecast_issued_at": "2026-11-02T12:15:00Z",
                "leakage_audit": {
                    "maximum_demand_context_month": "2026-10-01",
                    "maximum_e9_residual_target_month": "2021-12-01",
                    "maximum_e24b_label_available_at": "2026-05-01",
                    "maximum_weather_available_at": "2026-10-01T00:00:00Z",
                    "all_cutoffs_at_or_before_issuance": True,
                },
            }))
            validate_bundle(root)
            (root / "chronos2/config.json").write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                validate_bundle(root)


if __name__ == "__main__":
    unittest.main()
