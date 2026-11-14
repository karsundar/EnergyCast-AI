"""Source-specific artifact discovery and CDS request planning."""

from __future__ import annotations

import fnmatch
import importlib
import calendar
import netrc
import re
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen

from .http import open_with_retries


SUPPORTED_HANDLERS = {"fred", "ercot", "eia", "copernicus", "geopolitical_risk", "new_york_fed"}
PUBLICATION_DATE = re.compile(r"(?<!\d)(20\d{2})[-_/]?(0[1-9]|1[0-2])[-_/]?([0-3]\d)(?!\d)")


@dataclass(frozen=True)
class Artifact:
    dataset_id: str
    url: str
    format: str
    filename: str
    requested_start: str | None
    requested_end: str | None
    estimated_bytes: int | None = None
    requires_authentication: bool = False
    availability: str = "available"
    publication_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = None
            self._text = []


def _format_for_url(url: str) -> str | None:
    suffix = Path(unquote(urlparse(url).path)).suffix.lower().lstrip(".")
    return suffix if suffix in {"csv", "xls", "xlsx", "zip", "nc", "netcdf", "grib", "grib2"} else None


def _publication_date(value: str) -> str | None:
    match = PUBLICATION_DATE.search(value)
    return f"{match[1]}-{match[2]}-{match[3]}" if match else None


def discover_catalog(dataset: dict[str, Any], html: str) -> list[Artifact]:
    parser = LinkParser()
    parser.feed(html)
    allowed = {str(value).lower().lstrip(".") for value in dataset.get("allowed_formats", ())}
    pattern = str(dataset.get("file_pattern", "*"))
    artifacts: list[Artifact] = []
    seen_urls: set[str] = set()
    seen_filenames: set[str] = set()
    for href, label in parser.links:
        resolved = urljoin(str(dataset["url"]), href)
        filename = Path(unquote(urlparse(resolved).path)).name
        artifact_format = _format_for_url(resolved)
        if not filename or artifact_format not in allowed:
            continue
        if pattern and not (fnmatch.fnmatch(filename.lower(), pattern.lower()) or re.search(pattern, filename, re.I)):
            continue
        filename_key = filename.casefold()
        if resolved in seen_urls or filename_key in seen_filenames:
            continue
        seen_urls.add(resolved)
        seen_filenames.add(filename_key)
        artifacts.append(
            Artifact(
                str(dataset["id"]),
                resolved,
                artifact_format,
                filename,
                dataset.get("required_start"),
                dataset.get("required_end"),
                publication_date=_publication_date(f"{resolved} {filename} {label}"),
            )
        )
    if dataset.get("source_handler") == "ercot" and dataset.get("request_parameters", {}).get("artifact_type") == "historical_load":
        start_year = date.fromisoformat(str(dataset["required_start"])).year
        end_year = date.fromisoformat(str(dataset["required_end"])).year
        artifacts = [
            artifact for artifact in artifacts
            if (match := re.search(r"(?<!\d)(20\d{2})(?!\d)", artifact.filename))
            and start_year <= int(match[1]) <= end_year
        ]
    return artifacts


def _direct_artifact(dataset: dict[str, Any]) -> Artifact:
    filename = Path(str(dataset.get("filename") or unquote(urlparse(str(dataset["url"])).path))).name
    filename = filename or f"{dataset['id']}.{dataset['format']}"
    return Artifact(
        str(dataset["id"]),
        str(dataset["url"]),
        str(dataset["format"]),
        filename,
        dataset.get("required_start"),
        dataset.get("required_end"),
        requires_authentication=bool(dataset.get("requires_authentication", False)),
    )


def _cds_auth_available() -> bool:
    if Path.home().joinpath(".cdsapirc").is_file():
        return True
    try:
        return netrc.netrc().authenticators("cds.climate.copernicus.eu") is not None
    except (FileNotFoundError, netrc.NetrcParseError, OSError):
        return False


def discover_dataset(
    dataset: dict[str, Any], timeout: float = 60.0, opener: Any = urlopen
) -> list[Artifact]:
    strategy = str(dataset.get("download_strategy", "direct"))
    handler = dataset.get("source_handler")
    if handler is not None and handler not in SUPPORTED_HANDLERS:
        raise ValueError(f"{dataset['id']}: unsupported source_handler {handler!r}")
    if strategy == "direct":
        return [_direct_artifact(dataset)]
    if strategy == "cds_api":
        available = _cds_auth_available()
        return [
            Artifact(
                str(dataset["id"]),
                f"cdsapi://{dataset['request_parameters']['dataset']}",
                str(dataset["format"]),
                f"{dataset['id']}.{{chunk}}.{dataset['format']}",
                dataset.get("required_start"),
                dataset.get("required_end"),
                requires_authentication=True,
                availability="disabled" if not dataset.get("enabled", True) else ("available" if available else "authentication-required"),
            )
        ]
    request = Request(str(dataset["url"]), headers={"User-Agent": "EnergyCast/phase-1-data-acquisition"})
    with open_with_retries(request, timeout, opener=opener) as response:
        status = getattr(response, "status", 200)
        if status < 200 or status >= 300:
            raise ValueError(f"{dataset['id']}: catalog returned HTTP {status}")
        content_type = response.headers.get("Content-Type", "") if response.headers else ""
        if "html" not in content_type.lower():
            raise ValueError(f"{dataset['id']}: catalog response is not HTML")
        html = response.read().decode("utf-8", errors="replace")
    return discover_catalog(dataset, html)


def retrieve_cds_chunk(dataset: dict[str, Any], request: dict[str, Any], target: Path) -> None:
    """Submit one validated request chunk; credentials remain owned by cdsapi."""
    if not dataset.get("enabled", True):
        raise ValueError(f"{dataset['id']}: CDS execution is disabled pending request validation")
    if not _cds_auth_available():
        raise ValueError(f"{dataset['id']}: CDS authentication is not configured")
    try:
        cdsapi = importlib.import_module("cdsapi")
    except ImportError as exc:
        raise ValueError("cdsapi is required for CDS downloads") from exc
    client = cdsapi.Client(quiet=True)
    client.retrieve(str(dataset["request_parameters"]["dataset"]), request, str(target))


def plan_cds_requests(dataset: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Create bounded monthly requests without submitting them."""
    parameters = dataset["request_parameters"]
    if parameters.get("validated") is not True:
        raise ValueError(f"{dataset['id']}: CDS request parameters are not validated")
    start = date.fromisoformat(str(dataset["required_start"]))
    end = date.fromisoformat(str(dataset["required_end"]))
    planned: list[tuple[str, dict[str, Any]]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        request: dict[str, Any] = {
            "variable": list(parameters["variables"]),
            "year": str(year),
            "month": f"{month:02d}",
            "area": list(parameters["area"]),
            "data_format": parameters["data_format"],
        }
        if dataset["id"] == "era5_historical_weather":
            request["day"] = [f"{day:02d}" for day in range(1, calendar.monthrange(year, month)[1] + 1)]
            request["time"] = [f"{hour:02d}:00" for hour in range(24)]
            if "download_format" in parameters:
                request["download_format"] = parameters["download_format"]
        else:
            request.update({
                "originating_centre": parameters["originating_centre"],
                "system": parameters["system"],
                "product_type": parameters["product_type"],
                "leadtime_month": [str(value) for value in parameters["leadtime_month"]],
            })
        planned.append((str(dataset["file_pattern"]).format(year=year, month=f"{month:02d}"), request))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return planned
