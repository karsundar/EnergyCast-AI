# Data sources

## Seasonal candidates (Phase 5A; not acquired)

- Copernicus ECMWF SEAS5 system 51: primary; authenticated free CDS access,
  matched 1981-2016 hindcasts and 2017-present forecasts, six leads and members.
- Copernicus NCEP CFSv2 system 2: secondary matched-system comparison.
- NOAA CFSv2: free public GRIB2; NCEI historical and AWS operational access are
  candidates, but global AWS-object bulk transfer is not approved.
- CDS NCEP CFSv2 system 2 bounded samples passed with Texas subsetting and all
  six variables; it is approved as a secondary provider candidate, not for bulk
  acquisition yet.
- Climate normals: derived from ERA5 observations available before each origin
  and explicitly labeled for unsupported forecast leads.

Last discovery verification: 2026-11-15

| Source | Authoritative catalog or endpoint | Strategy | Discovery result | Blocker |
|---|---|---|---|---|
| ERCOT historical load | `https://www.ercot.com/gridinfo/load/load_hist` | Catalog discovery | All 23 annual files for 2004–2026 acquired; 1,601,272 regional hourly records normalized | One missing eight-zone 2016 interval; 2021 source-total reconciliation anomaly |
| ERCOT forecasts | `https://www.ercot.com/gridinfo/load/forecast` | Catalog discovery | Annual monthly peak/energy family selected; 2025–2044 adjusted forecast normalized | Units inferred from official workbook labels and documented |
| EIA-861M | `https://www.eia.gov/electricity/data/eia861m/` | Catalog discovery | Consolidated 1990–2009 and 2010–2026-08 workbooks acquired | Historical `OTHER` sector differs from current schema |
| FRED | `https://fred.stlouisfed.org/graph/fredgraph.csv` | Direct CSV | 11 configured FRED series acquired and validated | ALFRED vintages remain future work |
| New York Fed GSCPI | `https://www.newyorkfed.org/research/policy/gscpi` | Official producer workbook | 346 monthly rows acquired; 1998-01 through 2026-10 verified | Upstream `.xlsx` is an OLE/XLS workbook; stored with the truthful `.xls` suffix |
| ERA5 | CDS plus validated AWS Earthmover mirror | Texas-only annual subsets with provider/version provenance | 23 caches and 269 normalized months validated; coverage 2004-01 through 2026-05 | June–October 2026 await later source coverage and local-boundary hours; weather-zone geometry unresolved |
| Copernicus seasonal | `seasonal-monthly-single-levels` / `seasonal-original-single-levels` through CDS API | Texas-only initialization-month chunks; matching-system forecasts/hindcasts | Catalogue vocabulary and access requirements verified; disabled | Accept CC-BY and additional non-European licence, then validate a bounded member/lead-time sample |
| Geopolitical Risk | `https://www.matteoiacoviello.com/gpr.htm` | Catalog discovery | Monthly workbook acquired; 1900-01 through 2026-10 verified | Early-series missingness is structural and retained |

Discovery and acquisition ledgers preserve exact resolved URLs, checksums, request fingerprints, and publication dates where available. Phase 1C reused cached artifacts and downloaded only missing in-scope ERCOT annual load files. Phase 3A retained the bounded 2024 sample and complete January 2004 validation month; no climate bulk acquisition has started.

Phase 2A uses only the normalized ERCOT hourly data for baseline model inputs. EIA remains a separate nationwide-expansion dataset; economic and geopolitical sources are exported as separate feature stores but are not included in baseline fits. ERA5 weather remains outside model training; Copernicus seasonal remains disabled.

AWS Earthmover is anonymous/free and matched CDS within packing precision through its validated snapshot ending 2026-06-30. The acquired source caches are contiguous through 2026-06-01 05:00 UTC and normalized partitions are complete through May 2026. Official Copernicus ARCO was equivalent but slower and requires the CDS key. Google ARCO was fastest for sparse reads but its analysis-ready regridding produced small numerical differences, so it is not approved as a silent canonical substitute.

ERCOT's official weather page and media kit publish and define the eight weather zones, but the downloadable official asset is currently a JPEG map. No authoritative machine-readable polygon or county crosswalk was located, so no inferred geometry has been promoted into the project.

Subsequent Phase 3B discovery found ERCOT FAQ documentation explicitly identifying an Attachment C Excel county-to-weather-zone mapping in PUCT Project 52691. The attachment itself was not retrievable from the public index. The implemented mapping therefore remains a secondary approximation: the published Energy Economics replication crosswalk (Zenodo DOI `10.5281/zenodo.23653132`) joined to official 2024 Census county boundaries. It must be replaced or reconciled if Attachment C becomes available.
# Phase 5B seasonal sources

- Primary: Copernicus CDS seasonal monthly single levels, ECMWF SEAS5 centre
  `ecmwf`, system `51`; individual members, six leads, native 1° Texas subset.
- Secondary: the same CDS collection, NCEP CFSv2 centre `ncep`, system `2`.
- Fallback: leakage-safe climate normals for unsupported lead months 7–12.
- NOAA/NCEI direct CFSv2 is validated as accessible but excluded from bulk due
  to unavailable spatial subset services for the tested artifact.
- NOAA's anonymous `noaa-cfs-pds` AWS archive is validated as a bounded
  operational fallback. Indexed GRIB ranges reduce transfer by 90.843%, but a
  full member/lead reconstruction is highly fragmented and has no matching
  retrospective hindcast coverage, so no fallback bulk run has been started.
- Hindcasts are retrospective research data, not archived real-time forecasts;
  no synthetic release timestamps are assigned. See `docs/PHASE5B_EXECUTION.md`.
