#!/usr/bin/env python3
"""Bounded NOAA CFSv2 THREDDS discovery; downloads metadata only."""

from __future__ import annotations

import argparse
import json
from urllib.parse import urljoin
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


CATALOG = "https://www.ncei.noaa.gov/thredds/catalog/model-cfs-allfile-operational-forecast/monthly-means/{year}/{year}{month:02d}/catalog.xml"


XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


def parse_catalog(payload: bytes, catalog_url: str) -> tuple[list[dict[str, str | None]], list[str]]:
    """Return direct artifacts and child catalog URLs from a THREDDS catalog."""
    root = ET.fromstring(payload)
    datasets: list[dict[str, str | None]] = []
    references: list[str] = []
    for element in root.iter():
        path = element.attrib.get("urlPath")
        if path:
            datasets.append({"name": element.attrib.get("name"), "url_path": path})
        href = element.attrib.get(XLINK_HREF)
        if element.tag.endswith("catalogRef") and href:
            references.append(urljoin(catalog_url, href))
    return datasets, references


def retrieve_catalog(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "EnergyCast-AI/phase5a-bounded-discovery"})
    with urlopen(request, timeout=60) as response:
        return response.read()


def discover(year: int, month: int, max_catalogs: int) -> dict[str, object]:
    root_url = CATALOG.format(year=year, month=month)
    pending = [root_url]
    visited: list[str] = []
    datasets: list[dict[str, str | None]] = []
    total_bytes = 0
    while pending and len(visited) < max_catalogs:
        # Depth-first traversal reaches month/day/cycle artifacts without
        # spending the bounded catalog budget on every sibling day first.
        url = pending.pop()
        if url in visited:
            continue
        payload = retrieve_catalog(url)
        total_bytes += len(payload)
        visited.append(url)
        artifacts, references = parse_catalog(payload, url)
        datasets.extend(artifacts)
        pending.extend(reference for reference in references if reference not in visited and reference not in pending)
    return {"status": "discovered", "root_catalog": root_url, "catalogs_visited": visited,
            "catalog_bytes": total_bytes, "catalog_limit": max_catalogs,
            "pending_subcatalogs": len(pending), "artifact_count": len(datasets), "artifacts": datasets}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--month", type=int, default=1)
    parser.add_argument("--max-catalogs", type=int, default=5)
    parser.add_argument("--execute", action="store_true", help="retrieve catalog XML only; never downloads GRIB")
    options = parser.parse_args()
    if not options.execute:
        print(json.dumps({"status": "dry_run", "catalog": CATALOG.format(year=options.year, month=options.month),
                          "downloads_data": False}, indent=2))
        return 0
    if not 1 <= options.max_catalogs <= 20:
        raise ValueError("--max-catalogs must be between 1 and 20")
    print(json.dumps(discover(options.year, options.month, options.max_catalogs), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
