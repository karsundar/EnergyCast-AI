import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from forecasting.tft import (FORBIDDEN_DECODER_COLUMNS, TFTDataConfig,
                             assert_decoder_is_leakage_safe, build_datasets,
                             prepare_tft_frame)


class Phase2BTFTTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.features = pd.read_parquet(ROOT / "data" / "processed" / "ercot_monthly_features.parquet")

    def test_prepared_frame_excludes_target_derived_decoder_features(self):
        frame = prepare_tft_frame(self.features, "total_energy_mwh")
        self.assertFalse(FORBIDDEN_DECODER_COLUMNS & set(frame.columns))

    def test_dataset_decoder_roles_are_leakage_safe(self):
        frame = prepare_tft_frame(self.features, "total_energy_mwh")
        training, validation, cutoff = build_datasets(frame, TFTDataConfig())
        assert_decoder_is_leakage_safe(training)
        self.assertEqual(str(cutoff.date()), "2019-12-01")
        self.assertEqual(len(validation), 9)


if __name__ == "__main__":
    unittest.main()
