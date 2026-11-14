import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import io
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "download_datasets.py"
sys.path.insert(0, str(ROOT / "scripts"))

from acquisition.handlers import discover_catalog, plan_cds_requests, retrieve_cds_chunk  # noqa: E402
from acquisition.http import atomic_validated_download, open_with_retries  # noqa: E402
from acquisition.validation import validate_file  # noqa: E402


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass


class DownloadDatasetsTest(unittest.TestCase):
    def test_download_and_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            served = base / "served"
            served.mkdir()
            content = b"timestamp,value\n2026-01-01T00:00:00Z,42\n"
            (served / "sample.csv").write_bytes(content)

            handler = partial(QuietHandler, directory=str(served))
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)

            manifest = base / "datasets.json"
            manifest.write_text(
                json.dumps(
                    {
                        "datasets": [
                            {
                                "id": "sample",
                                "url": f"http://127.0.0.1:{server.server_port}/sample.csv",
                                "format": "csv",
                                "sha256": hashlib.sha256(content).hexdigest(),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output = base / "raw"
            ledger = base / "acquisition.json"
            command = [
                sys.executable,
                str(SCRIPT),
                "--manifest",
                str(manifest),
                "--output-dir",
                str(output),
                "--ledger",
                str(ledger),
            ]

            first = subprocess.run(command, check=False, capture_output=True, text=True)
            second = subprocess.run(command, check=False, capture_output=True, text=True)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("downloaded: sample", first.stdout)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("cached: sample", second.stdout)
            self.assertEqual((output / "sample" / "sample.csv").read_bytes(), content)

    def test_rejects_example_placeholders(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--manifest",
                str(ROOT / "config" / "datasets.example.json"),
            ],
            check=False,
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("placeholder", result.stderr)


class FakeResponse(io.BytesIO):
    def __init__(self, body=b"", status=200, content_type="application/octet-stream"):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class ValidationAndHandlersTest(unittest.TestCase):
    def test_valid_fred_csv_records_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fred.csv"
            path.write_text("observation_date,INDPRO\n2020-01-01,1\n2020-03-01,2\n", encoding="utf-8")
            result = validate_file(path, "csv", {
                "source_handler": "fred", "allowed_formats": ["csv"],
                "required_columns": ["observation_date", "INDPRO"],
            })
            self.assertEqual((result.actual_start, result.actual_end), ("2020-01-01", "2020-03-01"))
            self.assertTrue(result.revised_history_candidate)

    def test_rejects_html_with_http_200(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "bad.csv"
            opener = lambda request, timeout: FakeResponse(b"<html>error</html>", 200, "text/html")
            with self.assertRaisesRegex(ValueError, "HTML"):
                atomic_validated_download("https://example.test/data.csv", destination, "csv", {"allowed_formats": ["csv"]}, 1, opener)
            self.assertFalse(destination.exists())

    def test_valid_excel_workbook_and_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            xlsx = base / "book.xlsx"
            with zipfile.ZipFile(xlsx, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
                archive.writestr("xl/workbook.xml", "<workbook/>")
            self.assertEqual(validate_file(xlsx, "xlsx", {"allowed_formats": ["xlsx"]}).format, "xlsx")
            zipped = base / "data.zip"
            with zipfile.ZipFile(zipped, "w") as archive:
                archive.writestr("data.csv", "date,value\n2020-01-01,1\n")
            self.assertEqual(validate_file(zipped, "zip", {"allowed_formats": ["zip"]}).format, "zip")

    def test_corrupted_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.zip"
            path.write_bytes(b"PK corrupted")
            with self.assertRaisesRegex(ValueError, "invalid ZIP"):
                validate_file(path, "zip", {"allowed_formats": ["zip"]})

    def test_transient_503_and_429_are_retried(self):
        for status in (503, 429):
            calls = []
            def opener(request, timeout):
                calls.append(status)
                if len(calls) == 1:
                    raise HTTPError(request.full_url, status, "retry", {}, None)
                return FakeResponse(b"ok")
            with mock.patch("acquisition.http.time.sleep"):
                response = open_with_retries(Request("https://example.test"), 1, opener=opener)
            self.assertEqual(response.read(), b"ok")
            self.assertEqual(len(calls), 2)

    def test_catalog_parsing_and_duplicate_detection(self):
        html = ('<a href="/files/load_2024.xlsx">first</a>'
                '<a href="/files/load_2024.xlsx">duplicate URL</a>'
                '<a href="/mirror/load_2024.xlsx">duplicate filename</a>')
        artifacts = discover_catalog({
            "id": "ercot", "url": "https://example.test/catalog", "allowed_formats": ["xlsx"],
            "file_pattern": ".*load.*\\.xlsx$", "required_start": "2024-01-01", "required_end": "2024-12-31",
        }, html)
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0].url, "https://example.test/files/load_2024.xlsx")

    def test_ercot_catalog_is_limited_to_requested_years(self):
        html = ''.join(f'<a href="/load_{year}.xls">load {year}</a>' for year in (2003, 2004, 2026, 2027))
        artifacts = discover_catalog({
            "id": "ercot", "url": "https://example.test", "source_handler": "ercot",
            "request_parameters": {"artifact_type": "historical_load"}, "allowed_formats": ["xls"],
            "file_pattern": ".*load.*\\.xls$", "required_start": "2004-01-01", "required_end": "2026-12-31",
        }, html)
        self.assertEqual([artifact.filename for artifact in artifacts], ["load_2004.xls", "load_2026.xls"])

    def test_missing_cds_authentication(self):
        dataset = {"id": "era5", "enabled": True, "request_parameters": {"dataset": "era5"}}
        with mock.patch("acquisition.handlers._cds_auth_available", return_value=False):
            with self.assertRaisesRegex(ValueError, "authentication"):
                retrieve_cds_chunk(dataset, {}, Path("unused.nc"))

    def test_unvalidated_cds_parameters_cannot_be_planned(self):
        dataset = {"id": "era5", "required_start": "2020-01-01", "required_end": "2020-01-31",
                   "request_parameters": {"validated": False}}
        with self.assertRaisesRegex(ValueError, "not validated"):
            plan_cds_requests(dataset)

    def test_invalid_checksum_removes_download(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = {"id": "sample", "url": "https://example.test/sample.csv", "format": "csv",
                       "allowed_formats": ["csv"], "sha256": "0" * 64}
            destination = Path(directory) / "sample.csv"
            body = b"date,value\n2020-01-01,1\n"
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                atomic_validated_download(dataset["url"], destination, "csv", dataset, 1,
                                          lambda request, timeout: FakeResponse(body, content_type="text/csv"),
                                          expected_checksum="0" * 64)
            self.assertFalse(destination.exists())

    def test_interrupted_download_leaves_no_destination(self):
        class Interrupted(FakeResponse):
            def read(self, size=-1):
                raise OSError("connection lost")
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "data.csv"
            with self.assertRaisesRegex(OSError, "connection lost"):
                atomic_validated_download("https://example.test/data.csv", destination, "csv", {"allowed_formats": ["csv"]}, 1,
                                          lambda request, timeout: Interrupted())
            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(directory).glob("*.part")), [])

    def test_unsupported_response_format(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.bin"
            path.write_bytes(b"binary")
            with self.assertRaisesRegex(ValueError, "unsupported response format"):
                validate_file(path, "parquet", {"allowed_formats": ["csv"]})


if __name__ == "__main__":
    unittest.main()

