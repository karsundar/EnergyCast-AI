"""TFT datasets with observed future weather, explicitly for oracle experiments."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from pytorch_forecasting import TemporalFusionTransformer, TimeSeriesDataSet
from pytorch_forecasting.data import GroupNormalizer
from pytorch_forecasting.metrics import QuantileLoss

from .weather_oracle import DAILY_WEATHER_FEATURES, MONTHLY_WEATHER_FEATURES


def prepare_frame(source: pd.DataFrame, target: str, frequency: str) -> tuple[pd.DataFrame, list[str], list[str]]:
    frame = source.copy()
    if frequency == "monthly":
        frame["timestamp"] = pd.to_datetime(frame["month"])
        frame["time_idx"] = (frame["timestamp"].dt.year * 12 + frame["timestamp"].dt.month).astype(int)
        frame["month"] = frame["timestamp"].dt.month.astype(str)
        frame["quarter"] = frame["timestamp"].dt.quarter.astype(str)
        known_categoricals = ["month", "quarter"]
        weather = list(MONTHLY_WEATHER_FEATURES)
    else:
        frame["timestamp"] = pd.to_datetime(frame["date"])
        start = frame["timestamp"].min()
        frame["time_idx"] = (frame["timestamp"] - start).dt.days.astype(int)
        frame["month"] = frame["timestamp"].dt.month.astype(str)
        frame["day_of_week"] = frame["timestamp"].dt.dayofweek.astype(str)
        known_categoricals = ["month", "day_of_week"]
        weather = list(DAILY_WEATHER_FEATURES)
    for column in weather:
        frame[column] = frame[column].astype(float)
    frame["target"] = frame[target].astype(float)
    frame["geography"] = frame["geography"].astype(str)
    keep = ["timestamp", "time_idx", "geography", "target", *known_categoricals, *weather]
    return frame[keep].sort_values(["geography", "time_idx"]).reset_index(drop=True), known_categoricals, weather


def build_datasets(frame: pd.DataFrame, known_categoricals: list[str], weather: list[str],
                   encoder_length: int, decoder_length: int,
                   training_end: str, validation_end: str) -> tuple[TimeSeriesDataSet, TimeSeriesDataSet]:
    training_end_ts = pd.Timestamp(training_end)
    validation_end_ts = pd.Timestamp(validation_end)
    training = TimeSeriesDataSet(
        frame[frame["timestamp"] <= training_end_ts], time_idx="time_idx", target="target",
        group_ids=["geography"], max_encoder_length=encoder_length,
        max_prediction_length=decoder_length, static_categoricals=["geography"],
        time_varying_known_categoricals=known_categoricals,
        time_varying_known_reals=["time_idx", *weather], time_varying_unknown_reals=["target"],
        target_normalizer=GroupNormalizer(groups=["geography"], transformation="softplus"),
        add_relative_time_idx=True, add_target_scales=True, add_encoder_length=True,
        allow_missing_timesteps=False,
    )
    validation = TimeSeriesDataSet.from_dataset(
        training, frame[frame["timestamp"] <= validation_end_ts],
        min_prediction_idx=int(frame.loc[frame["timestamp"] > training_end_ts, "time_idx"].min()),
        predict=False, stop_randomization=True,
    )
    assert "target" not in training.time_varying_known_reals
    assert set(weather).issubset(training.time_varying_known_reals)
    return training, validation


def build_model(training: TimeSeriesDataSet, config: dict[str, Any]) -> TemporalFusionTransformer:
    return TemporalFusionTransformer.from_dataset(
        training, learning_rate=float(config["learning_rate"]), hidden_size=int(config["hidden_size"]),
        attention_head_size=int(config["attention_heads"]), lstm_layers=int(config["lstm_layers"]),
        hidden_continuous_size=int(config["hidden_continuous_size"]), dropout=float(config["dropout"]),
        output_size=len(config["quantiles"]), loss=QuantileLoss(quantiles=config["quantiles"]),
        reduce_on_plateau_patience=4, log_interval=-1,
    )


def assert_finite_frame(frame: pd.DataFrame, weather: list[str]) -> None:
    if not np.isfinite(frame[["target", *weather]].to_numpy(dtype=float)).all():
        raise ValueError("TFT input contains non-finite target or weather values")
