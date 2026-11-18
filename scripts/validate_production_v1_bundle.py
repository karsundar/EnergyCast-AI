#!/usr/bin/env python3
"""Validate frozen V1 artifacts and deterministic fresh-process inference."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np
import pandas as pd

from forecasting.frozen_bundle import sha256, validate_bundle

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=ROOT / "reports/production/v1/bundle_validation.json")
    options = parser.parse_args()
    manifest = validate_bundle(options.bundle)
    outputs = []
    try:
        for _ in range(2):
            handle = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
            handle.close()
            path = Path(handle.name)
            subprocess.run([
                str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/run_frozen_bundle_inference.py"),
                "--bundle", str(options.bundle), "--output", str(path),
            ], check=True, cwd=ROOT)
            outputs.append(path)
        first, second = (pd.read_parquet(path) for path in outputs)
        keys = ["target_name", "origin", "target_month", "horizon", "geography"]
        compared = first.merge(second, on=keys, suffixes=("_first", "_second"), validate="one_to_one")
        delta = float((compared.p50_first - compared.p50_second).abs().max())
        if delta != 0.0 or len(first) != 216 or not np.isfinite(first.p50).all():
            raise ValueError(f"frozen inference validation failed: rows={len(first)}, delta={delta}")
        if {"p10", "p90", "confidence"} & set(first):
            raise ValueError("public inference emitted probabilistic fields")
        report = {
            "status": "passed", "bundle_version": manifest["bundle_version"],
            "training_data_cutoff": manifest["training_data_cutoff"],
            "rows": len(first), "finite_predictions": int(np.isfinite(first.p50).sum()),
            "fresh_process_runs": 2, "maximum_absolute_difference": delta,
            "artifact_count": len(manifest["artifacts"]),
            "artifact_checksums_verified": True, "future_data_used": False,
            "public_probabilistic_intervals": False,
            "output_sha256": sha256(outputs[0]),
        }
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    finally:
        for path in outputs:
            path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
