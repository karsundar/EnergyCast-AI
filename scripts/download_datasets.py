#!/usr/bin/env python3
"""Discover and download configured EnergyCast datasets safely."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from acquisition.handlers import Artifact, discover_dataset
from acquisition.http import CHUNK_SIZE, atomic_validated_download
from acquisition.validation import validate_file


PLACEHOLDERS = ("replace-me", "replace-with-")
STRATEGIES = {"direct", "archive", "cds_api"}
HANDLERS = {"fred", "ercot", "eia", "copernicus", "geopolitical_risk", "new_york_fed"}
FORMATS = {"csv", "xls", "xlsx", "zip", "netcdf", "nc", "grib", "grib2"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_dataset(dataset: dict[str, Any]) -> None:
    dataset.setdefault("download_strategy", "direct")
    dataset.setdefault("allowed_formats", [dataset["format"]])
    dataset.setdefault("file_pattern", "*")
    dataset.setdefault("requires_authentication", False)
    dataset.setdefault("request_parameters", {})
    dataset.setdefault("enabled", True)


def load_manifest(path: Path) -> list[dict[str, Any]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read manifest {path}: {exc}") from exc
    datasets = document.get("datasets") if isinstance(document, dict) else None
    if not isinstance(datasets, list) or not datasets:
        raise ValueError("manifest must contain a non-empty 'datasets' list")
    seen: set[str] = set()
    for index, dataset in enumerate(datasets):
        if not isinstance(dataset, dict):
            raise ValueError(f"datasets[{index}] must be an object")
        missing = [key for key in ("id", "url", "format") if not dataset.get(key)]
        if missing:
            raise ValueError(f"datasets[{index}] is missing: {', '.join(missing)}")
        if any(marker in str(dataset[key]).lower() for key in ("id", "url", "format") for marker in PLACEHOLDERS):
            raise ValueError(f"datasets[{index}] still contains placeholder values")
        _normalize_dataset(dataset)
        dataset_id = str(dataset["id"])
        if dataset_id in seen:
            raise ValueError(f"duplicate dataset id: {dataset_id}")
        seen.add(dataset_id)
        strategy = str(dataset["download_strategy"])
        handler = dataset.get("source_handler")
        allowed = dataset["allowed_formats"]
        if strategy not in STRATEGIES:
            raise ValueError(f"{dataset_id}: unsupported download_strategy {strategy!r}")
        if handler is not None and handler not in HANDLERS:
            raise ValueError(f"{dataset_id}: unsupported source_handler {handler!r}")
        if not isinstance(allowed, list) or not allowed or any(str(value).lower() not in FORMATS for value in allowed):
            raise ValueError(f"{dataset_id}: allowed_formats must contain supported formats")
        if not isinstance(dataset["request_parameters"], dict):
            raise ValueError(f"{dataset_id}: request_parameters must be an object")
        if not isinstance(dataset["enabled"], bool) or not isinstance(dataset["requires_authentication"], bool):
            raise ValueError(f"{dataset_id}: enabled and requires_authentication must be booleans")
        if strategy == "cds_api" and not dataset["request_parameters"].get("dataset"):
            raise ValueError(f"{dataset_id}: CDS dataset identifier is required")
        checksum = dataset.get("sha256")
        if checksum is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", str(checksum)):
            raise ValueError(f"{dataset_id}: sha256 must be null or 64 hexadecimal characters")
    return datasets


def output_name(dataset: dict[str, Any]) -> str:
    configured = dataset.get("filename")
    name = Path(str(configured)).name if configured else Path(unquote(urlparse(str(dataset["url"])).path)).name
    return name or f"{dataset['id']}.{dataset['format']}"


def _result(dataset: dict[str, Any], artifact: Artifact, path: Path, checksum: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"id": dataset["id"], "url": artifact.url, "path": str(path), "bytes": path.stat().st_size,
            "sha256": checksum, "status": status, "format": artifact.format,
            "publication_date": artifact.publication_date, **extra}


def download_artifact(dataset: dict[str, Any], artifact: Artifact, output_dir: Path, timeout: float, force: bool) -> dict[str, Any]:
    destination = output_dir / str(dataset["id"]) / Path(artifact.filename).name
    expected = dataset.get("sha256") if artifact.url == dataset.get("url") else None
    if destination.exists() and not force:
        try:
            validation = validate_file(destination, artifact.format, dataset)
            actual = sha256_file(destination)
            if expected is None or actual.lower() == str(expected).lower():
                return _result(dataset, artifact, destination, actual, "cached",
                               actual_start=validation.actual_start, actual_end=validation.actual_end,
                               revised_history_candidate=validation.revised_history_candidate)
        except ValueError:
            pass
    validation, actual = atomic_validated_download(
        artifact.url, destination, artifact.format, dataset, timeout,
        expected_checksum=str(expected) if expected is not None else None,
        max_bytes=int(dataset["max_bytes"]) if dataset.get("max_bytes") is not None else None,
    )
    return _result(dataset, artifact, destination, actual, "downloaded",
                   actual_start=validation.actual_start, actual_end=validation.actual_end,
                   revised_history_candidate=validation.revised_history_candidate)


def download_one(dataset: dict[str, Any], output_dir: Path, timeout: float, force: bool) -> dict[str, Any]:
    """Backward-compatible direct-download entry point."""
    artifacts = discover_dataset(dataset, timeout)
    if len(artifacts) != 1:
        raise ValueError(f"{dataset['id']}: expected one direct artifact, found {len(artifacts)}")
    return download_artifact(dataset, artifacts[0], output_dir, timeout, force)


def result(dataset: dict[str, Any], path: Path, checksum: str, status: str) -> dict[str, Any]:
    """Compatibility wrapper retained for callers of the original script."""
    artifact = Artifact(str(dataset["id"]), str(dataset["url"]), str(dataset["format"]), path.name, None, None)
    return _result(dataset, artifact, path, checksum, status)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("config/datasets.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--ledger", type=Path, default=Path("reports/acquisition.json"))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dataset", action="append", help="limit work to one or more dataset IDs")
    parser.add_argument("--discover", action="store_true", help="resolve artifacts without downloading them")
    parser.add_argument("--dry-run", action="store_true", help="show planned artifacts without downloading them")
    parser.add_argument("--validate-manifest", action="store_true", help="validate configuration and exit")
    return parser.parse_args()


def _selected(datasets: list[dict[str, Any]], ids: list[str] | None) -> list[dict[str, Any]]:
    if not ids:
        return datasets
    wanted = set(ids)
    selected = [dataset for dataset in datasets if dataset["id"] in wanted]
    missing = wanted - {str(dataset["id"]) for dataset in selected}
    if missing:
        raise ValueError(f"unknown dataset ID(s): {', '.join(sorted(missing))}")
    return selected


def main() -> int:
    args = parse_args()
    try:
        datasets = load_manifest(args.manifest)
        selected = _selected(datasets, args.dataset)
        if args.validate_manifest:
            print(f"valid manifest: {len(datasets)} datasets")
            return 0
        records: list[dict[str, Any]] = []
        for dataset in selected:
            if not dataset["enabled"] and not (args.discover or args.dry_run):
                records.append({"id": dataset["id"], "status": "disabled"})
                continue
            artifacts = discover_dataset(dataset, args.timeout)
            if args.discover or args.dry_run:
                if not artifacts:
                    records.append({"id": dataset["id"], "status": "unavailable"})
                records.extend({**artifact.to_dict(), "status": "discovered"} for artifact in artifacts)
                continue
            if not artifacts:
                raise ValueError(f"{dataset['id']}: no downloadable artifacts discovered")
            records.extend(download_artifact(dataset, artifact, args.output_dir, args.timeout, args.force) for artifact in artifacts)
        ledger = {"created_at": datetime.now(timezone.utc).isoformat(), "manifest": str(args.manifest),
                  "mode": "discovery" if args.discover or args.dry_run else "download", "datasets": records}
        args.ledger.parent.mkdir(parents=True, exist_ok=True)
        args.ledger.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for record in records:
        if args.discover or args.dry_run:
            print(json.dumps(record, sort_keys=True))
        elif record.get("path"):
            print(f"{record['status']}: {record['id']} -> {record['path']}")
        else:
            print(f"{record['status']}: {record['id']}")
    print(f"ledger: {args.ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
