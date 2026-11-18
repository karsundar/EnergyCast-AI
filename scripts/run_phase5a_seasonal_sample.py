#!/usr/bin/env python3
"""Plan or execute one bounded CDS seasonal forecast/hindcast sample."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CONFIG = ROOT / "config" / "phase5a_seasonal.json"


def fingerprint(request: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()


def request_for(config: dict, provider_name: str, sample_kind: str) -> dict[str, object]:
    provider = config["providers"][provider_name]
    sample = provider[f"sample_{sample_kind}"]
    return {
        "originating_centre": provider["originating_centre"],
        "system": provider["system"],
        "variable": config["variables"],
        "product_type": ["monthly_mean"],
        "year": sample["year"],
        "month": sample["month"],
        "leadtime_month": sample["leadtime_month"],
        "area": config["texas_area_nwse"],
        "data_format": "netcdf"
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("copernicus_seas5", "copernicus_cfsv2"),
                        default="copernicus_seas5")
    parser.add_argument("--sample-kind", choices=("forecast", "hindcast"), default="forecast")
    parser.add_argument("--execute", action="store_true", help="submit exactly one bounded CDS request")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    provider = config["providers"][args.provider]
    request = request_for(config, args.provider, args.sample_kind)
    plan = {"mode": "execute" if args.execute else "dry_run", "dataset": provider["dataset"],
            "provider": args.provider, "sample_kind": args.sample_kind,
            "request": request, "fingerprint": fingerprint(request)}
    print(json.dumps(plan, indent=2))
    if not args.execute:
        return 0
    if not (Path.home() / ".cdsapirc").is_file():
        raise RuntimeError("missing ~/.cdsapirc; credentials are never accepted as command arguments")
    import cdsapi
    from scripts.weather.seasonal import validate_seasonal_netcdf
    prefix = "seas5" if args.provider == "copernicus_seas5" else "ncep_cfsv2"
    output = args.output or ROOT / "data" / "raw" / "weather" / "seasonal" / f"{prefix}_{args.sample_kind}_sample.nc"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temp_dir:
        temporary = Path(temp_dir) / "response"
        started = time.monotonic()
        cdsapi.Client().retrieve(provider["dataset"], request, str(temporary))
        members = []
        if zipfile.is_zipfile(temporary):
            with zipfile.ZipFile(temporary) as archive:
                for info in archive.infolist():
                    if info.is_dir() or Path(info.filename).is_absolute() or ".." in Path(info.filename).parts:
                        raise ValueError("unsafe seasonal archive member")
                    member = Path(temp_dir) / Path(info.filename).name
                    with archive.open(info) as source, member.open("wb") as target:
                        shutil.copyfileobj(source, target)
                    members.append(validate_seasonal_netcdf(member))
        else:
            members.append(validate_seasonal_netcdf(temporary))
        os.replace(temporary, output)
    ledger = {**plan, "path": str(output), "bytes": output.stat().st_size,
              "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "seconds": time.monotonic() - started,
              "members": members}
    output.with_suffix(output.suffix + ".json").write_text(json.dumps(ledger, indent=2) + "\n")
    print(json.dumps(ledger, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
