# Phase 5B seasonal acquisition handoff

## Primary completion

ECMWF SEAS5 priority acquisition completed on 2026-11-16. The completion audit
validated all 227 request fingerprints, raw SHA-256 checksums and normalized
partitions: 6,949,404 rows, 110,580,266 raw bytes, 437,198,418 processed bytes,
zero pending/invalid partitions and zero recorded failures. The run spanned
approximately 4 hours 49 minutes wall-clock.

CDS NCEP CFSv2 system 2 completed on 2026-11-17. The independent audit validates
all 227 request fingerprints, checksums, and normalized partitions: 11,483,472
rows, 169,094,473 raw bytes, 701,687,339 processed bytes, and zero pending or
invalid partitions. Two historical failed attempts remain in the progress audit,
but both affected requests were subsequently acquired and validated.

The completed primary data were aggregated through the approximate ERCOT 1°
weight matrix. Outputs contain 406,134 member-level regional rows and 12,258
ensemble-summary rows, with zero duplicate keys or missing dynamic forecast
values. Retrospective hindcasts retain 25 members and null availability;
archived forecasts retain 51 members. The operational feature file contains
7,668 rows: SEAS5 leads 1–6 plus expanding ERA5 climate-normal fallbacks for
leads 7–12. The fallback uses observations strictly before each forecast origin,
is explicitly labeled, and leaves unavailable dewpoint and ensemble quantiles
null. It does not fabricate daily extremes.

NCEP processing produced 671,112 regional member rows and 12,258 ensemble rows
with zero duplicate keys. Actual member counts vary by initialization: 20–28
for hindcasts and 112–124 for forecasts. The exact-common provider comparison
contains 12,258 rows from 2004-01 through 2026-11. Provider differences are
descriptive; the equal-provider mean remains explicitly uncalibrated.

## NOAA/AWS fallback validation

The anonymous `noaa-cfs-pds` operational archive was tested as a CDS-congestion
fallback. The correct all-time monthly object was range-downloaded from its GRIB
index: 479,775 of 5,239,300 bytes (90.843% reduction). All required native
fields were decoded on the 12×14 Texas subset. After linear regridding, the
matched CDS member differences were 0.106 K temperature MAE, about 0.04 m/s
U/V-wind MAE, below 1e-9 m/s precipitation-rate MAE and 0.136 W/m² solar-flux
MAE. Dewpoint derived from specific humidity and pressure differed by 0.743 K.

This validates the native operational source but not an economical bulk route.
Reconstructing every 2021–2026 CDS member and six leads requires roughly 52,000
objects and about 25 GB of selected-message caches. AWS also does not provide
the matching 2004–2016 retrospective hindcast set. Therefore it remains a
bounded fallback; both selected CDS providers are now complete.

## Scope and coverage

The default `priority` plan requests the six validated monthly variables, six
lead months, individual members and the Texas bounding box only. SEAS5 is
complete and validated.

| Provider | Product | Hindcast coverage | Archived forecast coverage | Priority scope |
|---|---|---|---|---|
| ECMWF SEAS5 | CDS centre `ecmwf`, system `51` | 1981-01 through 2016-12 | 2017-01 onward; operational system 51 from 2022-11 | hindcast 2004-01–2016-12; forecast 2021-01–2026-11 |
| NCEP CFSv2 | CDS centre `ncep`, system `2` | 1993-01 through 2016-12 | 2019-10 onward | hindcast 2004-01–2016-12; forecast 2021-01–2026-11 |

The 2026 end month reflects the catalogue available on 2026-11-15 and must be
rechecked before a later run. Hindcasts are labeled retrospective and retain a
null `available_at`; they are not valid archived real-time backtests. Archived
forecasts retain issue date, target month, lead, member, system, provenance and
the provider's documented release schedule (ECMWF day 6 at 12 UTC; other centres
day 10 at 12 UTC). This schedule is not represented as a file-specific audit
timestamp.

## Size and runtime estimates

Estimates scale the validated two-lead samples to six leads. They are planning
figures, not quotas or guarantees.

| Priority provider | Requests | Expected rows | Raw | Processed Parquet | Runtime |
|---|---:|---:|---:|---:|---:|
| SEAS5 | 227 (156 hindcast + 71 forecast) | 6,949,404 | 150,602,094 B (144 MiB) | 434,910,375 B (415 MiB) | 2.8–11.3 h plus queueing |
| CDS NCEP | 227 (156 + 71) | 12,170,928 | 216,726,582 B (207 MiB) | 754,021,014 B (719 MiB) | 2.8–11.3 h plus queueing |

Reserve at least 8 GiB for caches, atomic temporary files, provider variance and
future extensions. CDS publishes no numeric per-user request limit applicable to
this plan. Its protection guidance requires sequential submission; the runner
keeps one active request, backs off only transient 429/5xx/network errors, and
stops on licence, authentication or invalid-request failures.

## Weather feature limitations

ERA5 supplies observed hourly 0.25° weather. Seasonal data are monthly 1°
ensemble forecasts with six leads. Monthly seasonal means can support mean
temperature, humidity, vector wind, precipitation and solar-energy features.
They cannot supply daily minima/maxima, daily degree days, heatwave/cold-wave
runs or other daily extremes. Those fields remain unavailable rather than being
fabricated. Leads 7–12 use forecast-origin-safe climate normals and are labeled
as such.

The seasonal 1° weight matrix uses equal-area intersections with the existing
approximate ERCOT zone map. It is reusable, but remains non-authoritative until
an official machine-readable ERCOT mapping is obtained.

## Manual commands

All commands run from the repository root. Review the plans without network use:

```bash
.venv/bin/python scripts/run_phase5b_seasonal_bulk.py --provider seas5 --plan priority
.venv/bin/python scripts/run_phase5b_seasonal_bulk.py --provider ncep --plan priority
```

First run one bounded SEAS5 request. If it validates, launch the primary job:

```bash
.venv/bin/python scripts/run_phase5b_seasonal_bulk.py --provider seas5 --plan priority --execute --acknowledge-cds-policy --max-requests 1
.venv/bin/python scripts/run_phase5b_seasonal_bulk.py --provider seas5 --plan priority --execute --acknowledge-cds-policy
```

Background launch:

```bash
mkdir -p logs reports
nohup env PYTHONUNBUFFERED=1 .venv/bin/python scripts/run_phase5b_seasonal_bulk.py --provider seas5 --plan priority --execute --acknowledge-cds-policy > logs/phase5b_seas5.log 2>&1 & echo $! > reports/phase5b_seas5.pid
```

Monitor, stop safely, and resume with the same launch command:

```bash
tail -f logs/phase5b_seas5.log
kill -TERM "$(cat reports/phase5b_seas5.pid)"
nohup env PYTHONUNBUFFERED=1 .venv/bin/python scripts/run_phase5b_seasonal_bulk.py --provider seas5 --plan priority --execute --acknowledge-cds-policy > logs/phase5b_seas5.log 2>&1 & echo $! > reports/phase5b_seas5.pid
```

Validate the complete primary result, then optionally run the secondary provider
by replacing `seas5` with `ncep` and using a separate log/PID name:

```bash
.venv/bin/python scripts/validate_phase5b_seasonal_bulk.py --provider seas5 --plan priority
.venv/bin/python scripts/validate_phase5b_seasonal_bulk.py --provider ncep --plan priority
```

The progress ledger is persisted after every validated initialization month.
Reruns validate request fingerprints, file presence and SHA-256 before skipping;
an interrupted raw cache is normalized locally instead of resubmitted.

The runner explicitly sets the CDS library's internal retry count to one. This
prevents its default 500-attempt loop from holding a failed request indefinitely;
the runner then applies the configured four transient retries with exponential
backoff and records a terminal failure. Runs started before this correction must
be restarted to load it; all 168 already validated NCEP partitions remain cached.
Interrupted response bodies (`ChunkedEncodingError`/incomplete reads), connection
failures, decoding failures, timeouts, HTTP 429 and transient HTTP 5xx responses
are handled by the same bounded outer retry policy. Validation/schema failures
remain terminal rather than being retried.

Process or revalidate the completed SEAS5 regional features without network
access:

```bash
.venv/bin/python scripts/process_phase5b_seas5.py
```

The processing audit is in
`reports/phase5b_seas5_processing_validation.json`.

## Secondary processing handoff

NCEP regional processing and provider comparison are prepared but protected by
a completion gate. The processor freezes the validated progress-ledger entries
at startup, verifies each raw checksum, and ignores partitions subsequently
added by the active acquisition process. NCEP initialization member counts are
validated from the actual data rather than hardcoded; the first three completed
hindcasts contain 28, 24 and 20 members.

After the independent NCEP bulk job finishes, validate and process it:

```bash
.venv/bin/python scripts/validate_phase5b_seasonal_bulk.py --provider ncep --plan priority
.venv/bin/python scripts/process_phase5b_ncep.py
.venv/bin/python scripts/compare_phase5b_providers.py
```

The first command must report 227/227 before the processor will create official
regional artifacts. The comparison uses exact common issue, target, lead, zone
and forecast-kind keys. Its equal-provider mean is labeled as an uncalibrated
feature and must not be presented as a validated forecast ensemble.

Read-only monitoring remains:

```bash
tail -f logs/phase5b_ncep.log
.venv/bin/python scripts/validate_phase5b_seasonal_bulk.py --provider ncep --plan priority --allow-incomplete
```

## Authoritative references

- CDS seasonal catalogue: <https://cds.climate.copernicus.eu/datasets/seasonal-monthly-single-levels>
- C3S systems and coverage: <https://confluence.ecmwf.int/pages/viewpage.action?pageId=737568427>
- C3S multi-system description: <https://confluence.ecmwf.int/spaces/CKB/pages/77213502/Description%2Bof%2Bthe%2BC3S%2Bseasonal%2Bmulti-system>
- CDS protection measures: <https://forum.ecmwf.int/t/data-stores-service-protection-measures-affecting-data-downloads-and-account-access-cds-ads-ewds-ecds/15481>
