"""Leakage-safe Temporal Fusion Transformer dataset and model helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from pytorch_forecasting import TemporalFusionTransformer, TimeSeriesDataSet
from pytorch_forecasting.data import GroupNormalizer
from pytorch_forecasting.metrics import QuantileLoss


STATIC_CATEGORICALS = ["geography"]
KNOWN_CATEGORICALS = ["month_of_year", "quarter", "season"]
KNOWN_REALS = [
    "time_idx",
    "days_in_month",
    "weekday_count",
    "weekend_count",
    "holiday_count",
    "month_sin",
    "month_cos",
]
FORBIDDEN_DECODER_COLUMNS = {
    "historical_demand_lag_1",
    "historical_demand_lag_2",
    "historical_demand_lag_3",
    "historical_demand_lag_6",
    "historical_demand_lag_12",
    "historical_demand_lag_24",
    "historical_demand_trailing_mean_3",
    "historical_demand_trailing_mean_6",
    "historical_demand_trailing_mean_12",
    "historical_demand_trailing_mean_24",
    "historical_mom_growth",
    "historical_yoy_growth",
    "historical_peak_demand",
}


@dataclass(frozen=True)
class TFTDataConfig:
    encoder_length: int = 36
    prediction_length: int = 12
    batch_size: int = 32
    validation_end: str = "2020-12-01"


def prepare_tft_frame(features: pd.DataFrame, target_name: str) -> pd.DataFrame:
    """Select target plus origin-known calendar inputs; exclude derived future-demand features."""
    frame = features.loc[features["target_name"] == target_name].copy()
    if frame.empty:
        raise ValueError(f"no rows for target {target_name}")
    frame = frame.loc[frame["target_valid"]].copy()
    start = frame["month"].min()
    frame["time_idx"] = (
        (frame["month"].dt.year - start.year) * 12 + frame["month"].dt.month - start.month
    ).astype("int64")
    frame["month_of_year"] = frame["known_future_month_of_year"].astype(str)
    frame["quarter"] = frame["known_future_quarter"].astype(str)
    frame["season"] = frame["known_future_season"].astype(str)
    frame["days_in_month"] = frame["known_future_days_in_month"].astype(float)
    frame["weekday_count"] = frame["known_future_weekday_count"].astype(float)
    frame["weekend_count"] = frame["known_future_weekend_count"].astype(float)
    frame["holiday_count"] = frame["known_future_holiday_count"].astype(float)
    frame["month_sin"] = frame["known_future_month_sin"].astype(float)
    frame["month_cos"] = frame["known_future_month_cos"].astype(float)
    keep = ["month", "geography", "target_value", *KNOWN_CATEGORICALS, *KNOWN_REALS]
    result = frame[keep].sort_values(["geography", "time_idx"]).reset_index(drop=True)
    if not np.isfinite(result["target_value"]).all():
        raise ValueError("TFT target contains non-finite values")
    return result


def build_datasets(
    frame: pd.DataFrame, config: TFTDataConfig
) -> tuple[TimeSeriesDataSet, TimeSeriesDataSet, pd.Timestamp]:
    validation_end = pd.Timestamp(config.validation_end)
    validation_start = validation_end - pd.DateOffset(months=config.prediction_length - 1)
    training_cutoff = validation_start - pd.DateOffset(months=1)
    bounded = frame.loc[frame["month"] <= validation_end].copy()
    if bounded["month"].max() != validation_end:
        raise ValueError(f"validation end {validation_end.date()} is unavailable")
    training = TimeSeriesDataSet(
        bounded.loc[bounded["month"] <= training_cutoff],
        time_idx="time_idx",
        target="target_value",
        group_ids=["geography"],
        max_encoder_length=config.encoder_length,
        max_prediction_length=config.prediction_length,
        static_categoricals=STATIC_CATEGORICALS,
        time_varying_known_categoricals=KNOWN_CATEGORICALS,
        time_varying_known_reals=KNOWN_REALS,
        time_varying_unknown_reals=["target_value"],
        target_normalizer=GroupNormalizer(groups=["geography"], transformation="softplus"),
        add_relative_time_idx=True,
        add_target_scales=True,
        add_encoder_length=True,
        allow_missing_timesteps=False,
    )
    validation = TimeSeriesDataSet.from_dataset(
        training, bounded, min_prediction_idx=int(bounded.loc[bounded["month"] == validation_start, "time_idx"].min()),
        predict=True, stop_randomization=True
    )
    return training, validation, training_cutoff


def build_backtest_datasets(
    frame: pd.DataFrame,
    config: TFTDataConfig,
    training_end: str = "2017-12-01",
    validation_start: str = "2018-01-01",
    validation_end: str = "2020-12-01",
    test_start: str = "2021-01-01",
) -> tuple[TimeSeriesDataSet, TimeSeriesDataSet, TimeSeriesDataSet]:
    """Fit encoders/scalers on training only and create complete rolling windows."""
    training_end_ts = pd.Timestamp(training_end)
    validation_start_ts = pd.Timestamp(validation_start)
    validation_end_ts = pd.Timestamp(validation_end)
    test_start_ts = pd.Timestamp(test_start)
    training = TimeSeriesDataSet(
        frame.loc[frame["month"] <= training_end_ts],
        time_idx="time_idx",
        target="target_value",
        group_ids=["geography"],
        max_encoder_length=config.encoder_length,
        max_prediction_length=config.prediction_length,
        static_categoricals=STATIC_CATEGORICALS,
        time_varying_known_categoricals=KNOWN_CATEGORICALS,
        time_varying_known_reals=KNOWN_REALS,
        time_varying_unknown_reals=["target_value"],
        target_normalizer=GroupNormalizer(groups=["geography"], transformation="softplus"),
        add_relative_time_idx=True,
        add_target_scales=True,
        add_encoder_length=True,
        allow_missing_timesteps=False,
    )
    validation_bound = frame.loc[frame["month"] <= validation_end_ts]
    validation_idx = int(frame.loc[frame["month"] == validation_start_ts, "time_idx"].min())
    test_idx = int(frame.loc[frame["month"] == test_start_ts, "time_idx"].min())
    validation = TimeSeriesDataSet.from_dataset(
        training,
        validation_bound,
        min_prediction_idx=validation_idx,
        predict=False,
        stop_randomization=True,
    )
    test = TimeSeriesDataSet.from_dataset(
        training,
        frame,
        min_prediction_idx=test_idx,
        predict=False,
        stop_randomization=True,
    )
    return training, validation, test


def make_loaders(
    training: TimeSeriesDataSet, validation: TimeSeriesDataSet, batch_size: int
) -> tuple[Any, Any]:
    return (
        training.to_dataloader(train=True, batch_size=batch_size, num_workers=0),
        validation.to_dataloader(train=False, batch_size=batch_size, num_workers=0),
    )


def build_model(training: TimeSeriesDataSet, model_config: dict[str, Any], common: dict[str, Any]) -> TemporalFusionTransformer:
    return TemporalFusionTransformer.from_dataset(
        training,
        learning_rate=float(common["learning_rate"]),
        hidden_size=int(model_config["hidden_size"]),
        attention_head_size=int(model_config["attention_heads"]),
        lstm_layers=int(model_config["lstm_layers"]),
        hidden_continuous_size=int(model_config["hidden_continuous_size"]),
        output_size=len(common["quantiles"]),
        loss=QuantileLoss(quantiles=list(common["quantiles"])),
        dropout=0.1,
        reduce_on_plateau_patience=4,
        log_interval=-1,
    )


def assert_decoder_is_leakage_safe(training: TimeSeriesDataSet) -> None:
    known = set(training.time_varying_known_categoricals) | set(training.time_varying_known_reals)
    leaked = known & FORBIDDEN_DECODER_COLUMNS
    if leaked:
        raise AssertionError(f"target-derived decoder features detected: {sorted(leaked)}")
    if "target_value" in known:
        raise AssertionError("forecast target cannot be a known-future decoder feature")


def finite_gradients(model: torch.nn.Module) -> bool:
    gradients = [parameter.grad for parameter in model.parameters() if parameter.grad is not None]
    return bool(gradients) and all(bool(torch.isfinite(gradient).all()) for gradient in gradients)
