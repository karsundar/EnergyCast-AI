#!/usr/bin/env python3
"""Probe one CFSv2 artifact's NCSS metadata without downloading model data."""

from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


DEFAULT_PATH = ("model-cfs-allfile-operational-forecast/monthly-means/2024/202401/20240101/2024010106/"
                "flxf.01.2024010106.202402.avrg.grib.00Z.grb2")
BASE = "https://www.ncei.noaa.gov/thredds"


def endpoints(path: str) -> dict[str, str]:
    return {
        "direct": f"{BASE}/fileServer/{path}",
        "ncss_metadata": f"{BASE}/ncss/grid/{path}/dataset.xml",
        "ncss_query": f"{BASE}/ncss/grid/{path}",
        "opendap_dds": f"{BASE}/dodsC/{path}.dds",
        "opendap_data": f"{BASE}/dodsC/{path}",
    }


def bounded_fetch(url: str, limit: int = 5_000_000) -> tuple[bytes | None, dict[str, object]]:
    request = Request(url, headers={"User-Agent": "EnergyCast-AI/phase5a-bounded-probe"})
    try:
        with urlopen(request, timeout=60) as response:
            payload = response.read(limit + 1)
        if len(payload) > limit:
            return None, {"status": "rejected", "reason": f"metadata exceeded {limit} byte limit"}
        return payload, {"status": "available", "bytes": len(payload)}
    except HTTPError as error:
        return None, {"status": "unavailable", "http_status": error.code, "reason": str(error.reason)}
    except URLError as error:
        return None, {"status": "unavailable", "reason": str(error.reason)}


def direct_metadata(url: str) -> dict[str, object]:
    request = Request(url, method="HEAD", headers={"User-Agent": "EnergyCast-AI/phase5a-bounded-probe"})
    try:
        with urlopen(request, timeout=60) as response:
            return {"status": "available", "content_length": response.headers.get("Content-Length"),
                    "content_type": response.headers.get("Content-Type")}
    except (HTTPError, URLError) as error:
        return {"status": "unavailable", "reason": str(error)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url-path", default=DEFAULT_PATH)
    parser.add_argument("--execute", action="store_true", help="fetch NCSS XML metadata only")
    options = parser.parse_args()
    urls = endpoints(options.url_path)
    if not options.execute:
        print(json.dumps({"status": "dry_run", "downloads_data": False, **urls}, indent=2))
        return 0
    ncss_payload, ncss = bounded_fetch(urls["ncss_metadata"])
    variables = []
    if ncss_payload:
        root = ET.fromstring(ncss_payload)
        for element in root.iter():
            if element.tag.endswith("grid") or element.tag.endswith("variable"):
                name = element.attrib.get("name")
                if name:
                    variables.append(name)
    dds_payload, opendap = bounded_fetch(urls["opendap_dds"])
    dds_preview = dds_payload.decode("utf-8", errors="replace")[:4000] if dds_payload else None
    subset_supported = ncss["status"] == "available" or opendap["status"] == "available"
    print(json.dumps({"status": "metadata_available" if subset_supported else "no_subset_service",
                      "subset_supported": subset_supported, "ncss": ncss, "opendap": opendap,
                      "direct_file": direct_metadata(urls["direct"]),
                      "variable_count": len(set(variables)), "variables": sorted(set(variables)),
                      "opendap_dds_preview": dds_preview, **urls}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
