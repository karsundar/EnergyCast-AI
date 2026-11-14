"""Retrying HTTP and atomic validated download primitives."""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .validation import ValidationResult, validate_file


CHUNK_SIZE = 1024 * 1024
TRANSIENT_STATUSES = {429, 503}


def open_with_retries(
    request: Request,
    timeout: float,
    attempts: int = 3,
    backoff: float = 0.5,
    opener: Callable[..., Any] = urlopen,
) -> Any:
    for attempt in range(attempts):
        try:
            return opener(request, timeout=timeout)
        except HTTPError as exc:
            if exc.code not in TRANSIENT_STATUSES or attempt + 1 == attempts:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            delay = float(retry_after) if retry_after and retry_after.isdigit() else backoff * (2**attempt)
            time.sleep(delay)
    raise RuntimeError("retry loop exhausted")


def atomic_validated_download(
    url: str,
    destination: Path,
    expected_format: str,
    dataset: dict[str, Any],
    timeout: float,
    opener: Callable[..., Any] = urlopen,
    expected_checksum: str | None = None,
    max_bytes: int | None = None,
) -> tuple[ValidationResult, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    request = Request(safe_url(url), headers={"User-Agent": "EnergyCast/phase-1-data-acquisition"})
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".part",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            with open_with_retries(request, timeout, opener=opener) as response:
                status = getattr(response, "status", 200)
                if status < 200 or status >= 300:
                    raise ValueError(f"HTTP status {status}")
                content_type = response.headers.get("Content-Type") if response.headers else None
                content_length = response.headers.get("Content-Length") if response.headers else None
                if max_bytes is not None and content_length and int(content_length) > max_bytes:
                    raise ValueError(f"artifact exceeds size limit: {content_length} > {max_bytes} bytes")
                written = 0
                while chunk := response.read(CHUNK_SIZE):
                    written += len(chunk)
                    if max_bytes is not None and written > max_bytes:
                        raise ValueError(f"artifact exceeds size limit of {max_bytes} bytes")
                    temporary.write(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())
        validation = validate_file(temporary_path, expected_format, dataset, content_type)
        digest = hashlib.sha256()
        with temporary_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
                digest.update(chunk)
        checksum = digest.hexdigest()
        if expected_checksum is not None and checksum.lower() != expected_checksum.lower():
            raise ValueError(
                f"{dataset['id']}: checksum mismatch; expected {expected_checksum}, received {checksum}"
            )
        temporary_path.replace(destination)
        return validation, checksum
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
def safe_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, quote(parts.path), parts.query, parts.fragment))


