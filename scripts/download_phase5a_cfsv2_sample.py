#!/usr/bin/env python3
"""Download exactly one hard-capped CFSv2 GRIB2 validation artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
URL_PATH = ("model-cfs-allfile-operational-forecast/monthly-means/2024/202401/20240101/2024010106/"
            "flxf.01.2024010106.202402.avrg.grib.00Z.grb2")
URL = f"https://www.ncei.noaa.gov/thredds/fileServer/{URL_PATH}"
MAX_BYTES = 10 * 1024 * 1024


def validate_grib2(path: Path) -> int:
    data = path.read_bytes()
    offset = 0
    messages = 0
    while offset < len(data):
        if data[offset:offset + 4] != b"GRIB":
            raise ValueError(f"invalid GRIB signature at byte {offset}")
        edition = data[offset + 7]
        if edition != 2:
            raise ValueError(f"expected GRIB2, received edition {edition}")
        length = int.from_bytes(data[offset + 8:offset + 16], "big")
        if length <= 20 or offset + length > len(data):
            raise ValueError("invalid or incomplete GRIB2 message length")
        if data[offset + length - 4:offset + length] != b"7777":
            raise ValueError("invalid GRIB2 message terminator")
        offset += length
        messages += 1
    if offset != len(data) or messages == 0:
        raise ValueError("incomplete GRIB2 file")
    return messages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "raw" / "weather" / "seasonal" / "cfsv2_2024010106_202402_flxf.grb2")
    options = parser.parse_args()
    plan = {"url": URL, "maximum_bytes": MAX_BYTES, "output": str(options.output), "global_source": True}
    if not options.execute:
        print(json.dumps({"status": "dry_run", **plan}, indent=2))
        return 0
    options.output.parent.mkdir(parents=True, exist_ok=True)
    request = Request(URL, headers={"User-Agent": "EnergyCast-AI/phase5a-bounded-sample"})
    started = time.monotonic()
    with urlopen(request, timeout=120) as response:
        declared = int(response.headers.get("Content-Length", "0"))
        if declared <= 0 or declared > MAX_BYTES:
            raise RuntimeError(f"refusing Content-Length {declared}; limit is {MAX_BYTES}")
        with tempfile.NamedTemporaryFile(dir=options.output.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            remaining = declared
            while remaining:
                chunk = response.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise RuntimeError("interrupted CFSv2 download")
                temporary.write(chunk)
                remaining -= len(chunk)
    try:
        if temporary_path.stat().st_size != declared:
            raise RuntimeError("download size differs from Content-Length")
        messages = validate_grib2(temporary_path)
        checksum = hashlib.sha256(temporary_path.read_bytes()).hexdigest()
        os.replace(temporary_path, options.output)
    finally:
        temporary_path.unlink(missing_ok=True)
    ledger = {"status": "validated", **plan, "bytes": declared, "sha256": checksum,
              "grib_messages": messages, "seconds": time.monotonic() - started,
              "spatial_subsetting": "unavailable_from_ncei_for_this_artifact"}
    options.output.with_suffix(options.output.suffix + ".json").write_text(json.dumps(ledger, indent=2) + "\n")
    print(json.dumps(ledger, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
