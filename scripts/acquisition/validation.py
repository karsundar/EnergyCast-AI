"""Content-based validation for downloaded source artifacts."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


HTML_PREFIXES = (b"<!doctype html", b"<html", b"<?xml")
OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
NETCDF_SIGNATURES = (b"CDF\x01", b"CDF\x02", b"\x89HDF\r\n\x1a\n")
GRIB_SIGNATURE = b"GRIB"
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}")
CONTENT_TYPES = {
    "csv": ("text/csv", "text/plain", "application/csv", "application/octet-stream"),
    "xls": ("application/vnd.ms-excel", "application/octet-stream"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/zip", "application/octet-stream"),
    "zip": ("application/zip", "application/x-zip-compressed", "application/octet-stream"),
    "netcdf": ("application/x-netcdf", "application/netcdf", "application/octet-stream"),
    "nc": ("application/x-netcdf", "application/netcdf", "application/octet-stream"),
    "grib": ("application/x-grib", "application/octet-stream"),
    "grib2": ("application/x-grib", "application/octet-stream"),
}


@dataclass(frozen=True)
class ValidationResult:
    format: str
    columns: tuple[str, ...] = ()
    actual_start: str | None = None
    actual_end: str | None = None
    revised_history_candidate: bool = False


def _looks_like_html(prefix: bytes, content_type: str | None) -> bool:
    lowered = prefix.lstrip().lower()
    return any(lowered.startswith(marker) for marker in HTML_PREFIXES) or bool(
        content_type and "html" in content_type.lower()
    )


def _validate_csv(path: Path, dataset: dict[str, Any]) -> ValidationResult:
    try:
        text = path.read_text(encoding="utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        columns = tuple(reader.fieldnames or ())
        if not columns:
            raise ValueError("CSV has no header")
        required = tuple(str(value) for value in dataset.get("required_columns", ()))
        missing = [name for name in required if name not in columns]
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")
        date_column = next(
            (name for name in ("DATE", "date", "timestamp", "observation_date") if name in columns),
            None,
        )
        if dataset.get("source_handler") == "fred" and date_column is None:
            raise ValueError("FRED CSV is missing a DATE or observation_date column")
        dates: list[date] = []
        rows = 0
        for row in reader:
            rows += 1
            if date_column and row.get(date_column):
                value = str(row[date_column]).strip()
                if not DATE_PATTERN.match(value):
                    raise ValueError(f"invalid date value in {date_column}: {value!r}")
                dates.append(date.fromisoformat(value[:10]))
        if rows == 0:
            raise ValueError("CSV contains no data rows")
    except (UnicodeError, csv.Error, OSError) as exc:
        raise ValueError(f"CSV is not parseable: {exc}") from exc
    return ValidationResult(
        "csv",
        columns,
        min(dates).isoformat() if dates else None,
        max(dates).isoformat() if dates else None,
        dataset.get("source_handler") == "fred",
    )


def _validate_zip(path: Path, expected: str) -> ValidationResult:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if not names or archive.testzip() is not None:
                raise ValueError("ZIP archive is empty or corrupted")
            if expected == "xlsx" and not {
                "[Content_Types].xml",
                "xl/workbook.xml",
            }.issubset(names):
                raise ValueError("XLSX workbook structure is incomplete")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"invalid {expected.upper()} archive: {exc}") from exc
    return ValidationResult(expected)


def validate_file(
    path: Path,
    expected_format: str,
    dataset: dict[str, Any],
    content_type: str | None = None,
) -> ValidationResult:
    """Validate signature and minimum parseability without trusting the suffix."""
    expected = expected_format.lower().lstrip(".")
    allowed = {str(value).lower().lstrip(".") for value in dataset.get("allowed_formats", [expected])}
    if expected not in allowed:
        raise ValueError(f"unsupported response format {expected!r}; allowed: {sorted(allowed)}")
    try:
        prefix = path.read_bytes()[:512]
    except OSError as exc:
        raise ValueError(f"cannot read downloaded file: {exc}") from exc
    if not prefix:
        raise ValueError("downloaded file is empty")
    if _looks_like_html(prefix, content_type):
        raise ValueError(f"received HTML while expecting {expected}")
    if content_type:
        media_type = content_type.split(";", 1)[0].strip().lower()
        accepted = CONTENT_TYPES.get(expected, ())
        if accepted and media_type not in accepted:
            raise ValueError(f"unexpected Content-Type {media_type!r} for {expected}")

    if expected == "csv":
        return _validate_csv(path, dataset)
    if expected in {"zip", "xlsx"}:
        return _validate_zip(path, expected)
    if expected == "xls":
        if not prefix.startswith(OLE_SIGNATURE):
            raise ValueError("invalid XLS file signature")
        return ValidationResult(expected)
    if expected in {"netcdf", "nc"}:
        if not any(prefix.startswith(signature) for signature in NETCDF_SIGNATURES):
            raise ValueError("invalid NetCDF file signature")
        return ValidationResult("netcdf")
    if expected in {"grib", "grib2"}:
        if not prefix.startswith(GRIB_SIGNATURE):
            raise ValueError("invalid GRIB file signature")
        return ValidationResult(expected)
    raise ValueError(f"unsupported response format: {expected}")
