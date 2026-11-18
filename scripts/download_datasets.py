#!/usr/bin/env python3
"""Discover and download configured EnergyCast datasets safely."""

from __future__ import annotations

import argparse
import calendar
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from acquisition.handlers import Artifact, discover_dataset, plan_cds_requests, retrieve_cds_chunk
from acquisition.http import CHUNK_SIZE, atomic_validated_download
from acquisition.validation import validate_file
from weather.era5 import extract_netcdf_bundle_atomic, request_fingerprint


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


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _cds_sidecar(path: Path) -> Path:
    return path.with_name(f"{path.name}.request.json")


def _load_valid_cds_cache(
    destination: Path, dataset: dict[str, Any], fingerprint: str
) -> tuple[str, list[dict[str, Any]]] | None:
    sidecar = _cds_sidecar(destination)
    if not destination.exists() or not sidecar.exists():
        return None
    try:
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        if metadata.get("request_sha256") != fingerprint:
            return None
        validate_file(destination, str(dataset["format"]), dataset)
        checksum = sha256_file(destination)
        if metadata.get("sha256") != checksum:
            return None
        members = list(metadata.get("members", []))
        for member in members:
            member_path = Path(str(member["path"]))
            if not member_path.is_file() or sha256_file(member_path) != member.get("sha256"):
                return None
        return checksum, members
    except (KeyError, OSError, ValueError, json.JSONDecodeError, TypeError):
        return None


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


def download_cds(dataset: dict[str, Any], output_dir: Path, sample_only: bool, force: bool) -> list[dict[str, Any]]:
    """Retrieve validated CDS chunks atomically; sample mode submits exactly one bounded request."""
    requests = plan_cds_requests(dataset, validation_sample=sample_only)
    if sample_only:
        if not requests:
            raise ValueError(f"{dataset['id']}: no CDS requests planned")
        filename, request = requests[0]
        request = dict(request)
        sample = dataset["request_parameters"].get("sample_request", {})
        request.update(sample)
        sample_year = str(request["year"])
        sample_month = str(request["month"]).zfill(2)
        filename = f"sample_{str(dataset['file_pattern']).format(year=sample_year, month=sample_month)}"
        requests = [(filename, request)]
    records: list[dict[str, Any]] = []
    destination_dir = output_dir / str(dataset["id"])
    destination_dir.mkdir(parents=True, exist_ok=True)
    for filename, request in requests:
        planned_destination = destination_dir / Path(filename).name
        fingerprint = request_fingerprint(request)
        cache_candidates = [planned_destination]
        if planned_destination.suffix.lower() in {".nc", ".netcdf"}:
            cache_candidates.append(planned_destination.with_suffix(".zip"))
        if not force:
            cached = next(
                ((candidate, result) for candidate in cache_candidates
                 if (result := _load_valid_cds_cache(candidate, dataset, fingerprint)) is not None),
                None,
            )
            if cached is not None:
                destination, (checksum, members) = cached
                records.append(_result(
                    dataset, Artifact(str(dataset["id"]), f"cdsapi://{dataset['request_parameters']['dataset']}",
                                      str(dataset["format"]), destination.name, dataset.get("required_start"),
                                      dataset.get("required_end"), requires_authentication=True),
                    destination, checksum, "cached", request=request, request_sha256=fingerprint,
                    members=members,
                ))
                continue
        destination = planned_destination
        temporary = destination.with_name(f".{destination.name}.part")
        # A stale partial file can only be the result of an interrupted prior attempt.
        temporary.unlink(missing_ok=True)
        try:
            retrieve_cds_chunk(dataset, request, temporary, validation_sample=sample_only)
            with temporary.open("rb") as downloaded:
                response_signature = downloaded.read(4)
            if str(dataset["format"]).lower() in {"netcdf", "nc"} and response_signature == b"PK\x03\x04":
                destination = destination.with_suffix(".zip")
            max_bytes = dataset.get("max_bytes")
            if max_bytes is not None and temporary.stat().st_size > int(max_bytes):
                raise ValueError(f"{dataset['id']}: CDS response exceeds max_bytes")
            validation = validate_file(temporary, str(dataset["format"]), dataset)
            checksum = sha256_file(temporary)
            temporary.replace(destination)
            if response_signature == b"PK\x03\x04":
                members = extract_netcdf_bundle_atomic(destination, destination.with_suffix(""))
            else:
                members = [{
                    "name": destination.name,
                    "path": str(destination),
                    "compressed_bytes": destination.stat().st_size,
                    "bytes": destination.stat().st_size,
                    "compression_ratio": 1.0,
                    "sha256": checksum,
                }]
            _write_json_atomic(_cds_sidecar(destination), {
                "dataset_id": dataset["id"], "dataset": dataset["request_parameters"]["dataset"],
                "request": request, "request_sha256": fingerprint,
                "path": str(destination), "sha256": checksum, "members": members,
            })
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        records.append(_result(
            dataset, Artifact(str(dataset["id"]), f"cdsapi://{dataset['request_parameters']['dataset']}",
                              str(dataset["format"]), destination.name, dataset.get("required_start"),
                              dataset.get("required_end"), requires_authentication=True),
            destination, checksum, "downloaded", request=request, request_sha256=fingerprint,
            members=members,
            actual_start=validation.actual_start, actual_end=validation.actual_end,
        ))
    return records


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
    parser.add_argument("--start-month", help="inclusive CDS month bound in YYYY-MM format")
    parser.add_argument("--end-month", help="inclusive CDS month bound in YYYY-MM format")
    cds = parser.add_mutually_exclusive_group()
    cds.add_argument("--cds-sample", action="store_true", help="submit one bounded validated CDS sample request")
    cds.add_argument("--cds-bulk", action="store_true", help="submit every planned CDS chunk after sample validation")
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


def _bounded_cds_dataset(dataset: dict[str, Any], start_month: str | None, end_month: str | None) -> dict[str, Any]:
    if not start_month and not end_month:
        return dataset
    if dataset.get("download_strategy") != "cds_api":
        raise ValueError("--start-month/--end-month are supported only for CDS datasets")
    start_text = start_month or str(dataset["required_start"])[:7]
    end_text = end_month or str(dataset["required_end"])[:7]
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", start_text) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", end_text):
        raise ValueError("month bounds must use YYYY-MM")
    start_year, start_value = map(int, start_text.split("-"))
    end_year, end_value = map(int, end_text.split("-"))
    if (start_year, start_value) > (end_year, end_value):
        raise ValueError("start month cannot be after end month")
    bounded = copy.deepcopy(dataset)
    bounded["required_start"] = f"{start_text}-01"
    bounded["required_end"] = f"{end_text}-{calendar.monthrange(end_year, end_value)[1]:02d}"
    return bounded


def main() -> int:
    args = parse_args()
    try:
        datasets = load_manifest(args.manifest)
        selected = [
            _bounded_cds_dataset(dataset, args.start_month, args.end_month)
            for dataset in _selected(datasets, args.dataset)
        ]
        if args.validate_manifest:
            print(f"valid manifest: {len(datasets)} datasets")
            return 0
        records: list[dict[str, Any]] = []
        for dataset in selected:
            # A bounded CDS sample is the validation gate that permits enabling
            # the dataset, so it must be runnable while bulk execution is disabled.
            if not dataset["enabled"] and not (args.discover or args.dry_run or args.cds_sample):
                records.append({"id": dataset["id"], "status": "disabled"})
                continue
            artifacts = discover_dataset(dataset, args.timeout)
            if args.discover or args.dry_run:
                if not artifacts:
                    records.append({"id": dataset["id"], "status": "unavailable"})
                records.extend({**artifact.to_dict(), "status": "discovered"} for artifact in artifacts)
                continue
            if dataset.get("download_strategy") == "cds_api":
                if not (args.cds_sample or args.cds_bulk):
                    raise ValueError(f"{dataset['id']}: use --cds-sample before explicit --cds-bulk")
                records.extend(download_cds(dataset, args.output_dir, args.cds_sample, args.force))
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
