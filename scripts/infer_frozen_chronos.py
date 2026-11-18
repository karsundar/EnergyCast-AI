#!/usr/bin/env python3
"""Fresh-process Chronos-2 inference for a frozen EnergyCast bundle."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import torch
from chronos import Chronos2Pipeline


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if not torch.cuda.is_available() or torch.version.hip is None:
        raise RuntimeError("frozen Chronos inference requires the verified ROCm GPU; CPU fallback disabled")
    context = pd.read_parquet(options.bundle / "training_context.parquet")
    context = context.rename(columns={"geography": "item_id", "month": "timestamp", "total_energy_mwh": "target"})
    pipeline = Chronos2Pipeline.from_pretrained(
        options.bundle / "chronos2", local_files_only=True, device_map="cuda", dtype=torch.bfloat16,
    )
    prediction = pipeline.predict_df(
        context[["item_id", "timestamp", "target"]], prediction_length=12,
        quantile_levels=[0.1, 0.5, 0.9], id_column="item_id", timestamp_column="timestamp",
        target="target", batch_size=64,
    ).rename(columns={"item_id": "geography", "timestamp": "target_month", "0.5": "p50"})
    prediction[["geography", "target_month", "p50"]].to_parquet(options.output, index=False, compression="zstd")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
