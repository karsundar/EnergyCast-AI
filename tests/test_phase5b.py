import json
from pathlib import Path
import tempfile

import pandas as pd
import requests

from scripts.run_phase5b_seasonal_bulk import (
    create_cds_client, estimate, fingerprint, is_retryable_exception, request_for, tasks,
)
from scripts.process_phase5b_ncep import sql_path_list, validated_snapshot


ROOT = Path(__file__).resolve().parents[1]


def test_priority_plan_counts_and_rows():
    config = json.loads((ROOT / "config" / "phase5b_seasonal_bulk.json").read_text())
    summary = estimate(config, "priority")
    assert summary["requests"] == 227
    assert summary["hindcast_requests"] == 156
    assert summary["forecast_requests"] == 71
    assert summary["expected_normalized_rows"] == 6_949_404
    assert len(tasks(config, "priority")) == 227


def test_request_is_texas_bounded_and_stable():
    config = json.loads((ROOT / "config" / "phase5b_seasonal_bulk.json").read_text())
    _, date = tasks(config, "priority")[0]
    request = request_for(config, date)
    assert request["area"] == [36.5, -106.75, 25.75, -93.5]
    assert request["leadtime_month"] == ["1", "2", "3", "4", "5", "6"]
    assert fingerprint(config["dataset"], request) == fingerprint(config["dataset"], request)


def test_secondary_ncep_priority_plan_is_bounded():
    config = json.loads((ROOT / "config" / "phase5b_ncep_bulk.json").read_text())
    summary = estimate(config, "priority")
    assert summary["requests"] == 227
    assert summary["hindcast_requests"] == 156
    assert summary["forecast_requests"] == 71
    assert summary["expected_normalized_rows"] == 12_170_928


def test_processed_seas5_preserves_members_and_hindcast_availability():
    path = ROOT / "data/processed/weather/seasonal/regional/seas5_member_monthly.parquet"
    frame = pd.read_parquet(path)
    keys = ["issue_date", "lead_month", "ensemble_member", "weather_zone"]
    assert not frame.duplicated(keys).any()
    hindcasts = frame[frame.forecast_kind == "retrospective_hindcast"]
    forecasts = frame[frame.forecast_kind == "archived_realtime_forecast"]
    assert not hindcasts.available_at.notna().any()
    assert set(hindcasts.groupby("issue_date").ensemble_member.nunique()) == {25}
    assert set(forecasts.groupby("issue_date").ensemble_member.nunique()) == {51}


def test_processed_seas5_target_month_and_operational_leads():
    path = ROOT / "data/processed/weather/seasonal/regional/seas5_operational_12month.parquet"
    frame = pd.read_parquet(path)
    keys = ["issue_date", "lead_month", "weather_zone"]
    assert not frame.duplicated(keys).any()
    assert set(frame.lead_month) == set(range(1, 13))
    issue_month = pd.to_datetime(frame.issue_date, utc=True).dt.tz_localize(None).dt.to_period("M")
    expected = issue_month + (frame.lead_month.astype(int) - 1)
    actual = pd.to_datetime(frame.target_month).dt.to_period("M")
    assert (actual == expected).all()


def test_climate_normal_fallback_is_explicit_and_bounded():
    path = ROOT / "data/processed/weather/seasonal/regional/seas5_operational_12month.parquet"
    frame = pd.read_parquet(path)
    fallback = frame[frame.forecast_kind == "climate_normal_fallback"]
    assert set(fallback.lead_month) == set(range(7, 13))
    assert set(fallback.source_provider) == {"era5_expanding_normal"}
    assert fallback.dewpoint_mean_c_mean.isna().all()
    assert not any("minimum" in column or "maximum" in column for column in frame.columns)


def test_ncep_snapshot_uses_only_validated_entries():
    with tempfile.TemporaryDirectory() as directory:
        tmp_path = Path(directory)
        raw = tmp_path / "raw.nc"
        processed = tmp_path / "part.parquet"
        raw.write_bytes(b"validated raw")
        processed.write_bytes(b"validated parquet placeholder")
        import hashlib
        progress = {"entries": {
            "copernicus_cfsv2:hindcast:2004-01": {
                "status": "validated", "raw_path": str(raw), "processed_path": str(processed),
                "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
            },
            "copernicus_cfsv2:hindcast:2004-02": {"status": "running"},
        }}
        assert validated_snapshot(progress) == [processed.resolve()]


def test_ncep_sql_path_list_escapes_quotes():
    rendered = sql_path_list([Path("/tmp/a'b.parquet"), Path("/tmp/c.parquet")])
    assert rendered == "['/tmp/a''b.parquet','/tmp/c.parquet']"


def test_cds_client_uses_bounded_internal_retries(monkeypatch=None):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import cdsapi
    original = cdsapi.Client
    cdsapi.Client = FakeClient
    try:
        create_cds_client({"retry_base_seconds": 30})
    finally:
        cdsapi.Client = original
    assert captured == {"retry_max": 1, "sleep_max": 30}


def test_interrupted_http_body_is_retryable():
    assert is_retryable_exception(requests.exceptions.ChunkedEncodingError("incomplete body"))
    assert is_retryable_exception(requests.exceptions.ReadTimeout("timed out"))
    response = requests.Response()
    response.status_code = 503
    assert is_retryable_exception(requests.HTTPError(response=response))
    assert not is_retryable_exception(ValueError("invalid normalized data"))
