# Phase 5A seasonal weather acquisition

Phase 5A is prepared, but no seasonal request has been submitted. Requests are
Texas-bounded and disabled by default.

## Provider decision

Use Copernicus CDS ECMWF SEAS5 system 51 as the primary source. It supplies a
matched 1981-2016 hindcast and 2017-present forecast family on a 1° grid,
preserves ensemble members, and supplies six monthly leads. The six variables
map to ERA5 temperature, dewpoint/RH, U/V wind, precipitation and solar fields.

Copernicus NCEP CFSv2 is a secondary comparison (1993-2016 hindcasts and
operational availability from October 2019). NOAA CFSv2 offers free GRIB2
reforecasts from late 1981 through March 2011 and operational data from April
2011, nominally to nine months. Its anonymous AWS archive contains global
objects, so it remains blocked until bounded NCEI subsetting is demonstrated.

For unsupported leads, use an explicitly labeled climate-normal baseline. It
uses calendar-month observations strictly earlier than the issue date.

Sources: [Copernicus catalogue](https://cds.climate.copernicus.eu/datasets/seasonal-monthly-single-levels),
[system availability](https://confluence.ecmwf.int/pages/viewpage.action?navigatingVersions=true&pageId=737568427),
[SEAS5 guide](https://www.ecmwf.int/sites/default/files/elibrary/2021/20150-seas5-user-guide.pdf),
[NOAA CFS archive](https://www.ncei.noaa.gov/products/weather-climate-models/climate-forecast-system),
and [NOAA AWS registry](https://registry.opendata.aws/noaa-cfs/).

## Leakage and access gates

- Preserve issue and actual availability dates, target month, lead, system,
  version, ensemble member and hindcast/real-time status.
- ECMWF CDS is normally available on the 6th at 12 UTC; NCEP is normally
  available on the 10th. Backtests must honor recorded availability.
- Never mix operational and hindcast systems or substitute ERA5 observations.
- CDS requires a free account, `~/.cdsapirc`, CC-BY acceptance and acceptance
  of the non-European-contribution terms. Credentials remain outside Git.

## Exact bounded commands

Dry-run both requests:

```bash
cd /home/karthiks/src/EnergyCast-AI
.venv/bin/python scripts/run_phase5a_seasonal_sample.py --sample-kind forecast
.venv/bin/python scripts/run_phase5a_seasonal_sample.py --sample-kind hindcast
```

After accepting both licence layers, submit one bounded sample at a time:

```bash
.venv/bin/python scripts/run_phase5a_seasonal_sample.py --sample-kind forecast --execute
.venv/bin/python scripts/run_phase5a_seasonal_sample.py --sample-kind hindcast --execute
```

Each request contains six variables, two leads and the Texas box. NetCDF
dimensions, variables, members and leads are validated before atomic retention;
a request fingerprint and SHA-256 ledger are written. No bulk command exists.

## Bounded validation result

The January 2016 hindcast and January 2024 archived forecast passed. They share
the same Texas 11×14, 1° grid, six variables, units and lead months 1-2. The
hindcast contains members 0-24; the forecast contains 0-50. All values are
finite with zero missing observations. CDS returned NetCDF4/HDF5; the original
files are preserved despite the first runner-generated `.zip` suffix, and
future outputs use `.nc`.

The manual GRIB sample is a valid global 360×180 product, so manual form output
is not approved for bulk acquisition. API Texas subsetting is confirmed. The
machine report is `../reports/phase5a_seasonal_sample_validation.json`.

Normalized outputs preserve grid cell, member, initialization, availability,
target month and lead. Temperature/dewpoint are converted from K, RH is derived
from both, wind speed from U/V, precipitation rate to monthly mm, and mean solar
flux to monthly MJ/m². Climate normals cover origins 2018-01 through 2026-06
and use only earlier observations.

NOAA CFSv2 discovery is metadata-only and bounded. NCEI catalogs are nested as
month/day/cycle/artifacts, so the runner follows one depth-first branch through
at most five catalogs by default:

```bash
.venv/bin/python scripts/discover_phase5a_cfsv2.py --execute --max-catalogs 5
```

It never downloads GRIB data. A CFSv2 sample remains blocked until discovery
identifies exact flux artifacts and proves spatial subsetting rather than
global-object transfer.

Probe only the selected monthly flux artifact's NCSS metadata:

```bash
.venv/bin/python scripts/probe_phase5a_cfsv2_subset.py --execute
```

The probe has a 5 MB response cap and does not request data values. If NCSS is
available and exposes the required variables, the subsequent bounded sample
will request only the Texas box from that endpoint.

Observed result: both NCSS and OPeNDAP return HTTP 500 for the selected file.
Only the 5,303,426-byte global GRIB2 is available. One bounded sample may be
downloaded with a 10 MiB hard cap, atomic write, signature validation and SHA:

```bash
.venv/bin/python scripts/download_phase5a_cfsv2_sample.py --execute
```

This does not approve CFSv2 bulk acquisition. At four cycles per day and many
lead files, global-file transfer scales poorly; SEAS5 remains primary.

The bounded file validated as GRIB2 with 101 messages. It has 72,960 global
Gaussian-grid points but only 168 (0.2303%) in the Texas box. The flux family
contains temperature, specific humidity, surface pressure, precipitation,
solar flux and 10 m U wind, but no 10 m V wind. Dewpoint/RH can be derived;
wind speed cannot be reconstructed from this file alone. The NOAA direct-file
route is therefore retained only as a provider sample and rejected for bulk.

The next bounded comparison uses NCEP CFSv2 system 2 through CDS, which can
apply the Texas subset and harmonize the six-variable schema. Inspect first:

```bash
.venv/bin/python scripts/run_phase5a_seasonal_sample.py \
  --provider copernicus_cfsv2 --sample-kind forecast
```

Execution remains manual and sequential after request inspection and licence
confirmation. No CFSv2 bulk request is authorized.

## Final bounded provider result

The matching CDS NCEP system 2 pair passed: 28 hindcast members and 124
archived-forecast members on the same Texas 11×14 grid, six variables and two
leads, with no missing values. Normalized outputs contain 8,624 and 38,192 rows.

SEAS5 system 51 remains primary because it has the longer matched hindcast
(1981-2016), earlier operational continuity and a shorter publication delay.
CDS NCEP CFSv2 is the secondary comparison. NOAA direct files remain sample-only.
Retrospective hindcasts have `available_at = null`; only archived operational
forecasts receive scheduled release timestamps. This prevents treating a
retrospective hindcast as data that was actually available at a historical
forecast origin.

The bounded provider comparison is
`../reports/phase5a_provider_comparison.json`. No bulk acquisition is approved.

## Preliminary estimates

These are planning ranges pending measured samples. CDS and NOAA access are
free. Texas-only SEAS5 through 2026 is estimated at 1-3 GiB compressed, 2-5
GiB extracted and 0.5-1.5 GiB normalized; reserve 8 GiB temporary capacity.
A sample may take 1-10 minutes plus queue time; a later full archive may take
2-12 hours. NCEI-subset CFSv2 is tentatively 2-8 GiB. Global AWS acquisition
could be far larger and is not approved. Use SEAS5 for leads 1-6 and
leakage-safe normals for 7-12; evaluate CFSv2 leads 7-9 only after its sample.
