# Data licenses, source terms, and attribution

EnergyCast AI acquires and processes data from independent providers. The
project's Apache License 2.0 applies to original EnergyCast code and
documentation only. It does **not** grant rights to redistribute upstream data,
third-party workbooks, transformed source extracts, or model artifacts.

This inventory records the current technical understanding of source terms. It
is not legal advice. Provider terms at the time of access and distribution are
controlling.

## Source inventory

| Source | EnergyCast use | Terms and attribution status | Redistribution guidance |
|---|---|---|---|
| ERCOT historical load and forecasts | Hourly demand targets and official forecast references | Acquired from official ERCOT catalogues. No per-artifact open-data license is recorded in the repository. [ERCOT website terms](https://www.ercot.com/help/terms) apply. | Do not redistribute raw workbooks or substantial extracts without confirming the applicable ERCOT terms. Cite ERCOT and preserve source URLs, filenames, dates, and checksums. |
| U.S. EIA-861M | Separate monthly retail-sales dataset | EIA states that its U.S. government publications and data are public domain, subject to third-party-content and trademark exceptions. [EIA copyrights and reuse](https://www.eia.gov/about/copyrights_reuse.php). | Attribution is recommended: identify EIA, the publication/dataset, retrieval date, and original URL. Do not imply permission to use the EIA logo. |
| FRED series | Economic indicators | FRED is a retrieval service for series from multiple providers. FRED attribution does not replace underlying-series restrictions. [FRED terms](https://fred.stlouisfed.org/legal/). | Review every series' provider notes before redistribution or commercial use. Retain copyright notices and identify FRED as the retrieval service. |
| New York Fed GSCPI | Global Supply Chain Pressure Index | Acquired from the official New York Fed page. The workbook does not carry a repository-recorded open-data license. [GSCPI source](https://www.newyorkfed.org/research/policy/gscpi). | Cite the Federal Reserve Bank of New York and the source page. Do not redistribute the upstream workbook until the applicable reuse terms have been confirmed. |
| Geopolitical Risk Index | Monthly geopolitical-risk indicator | Acquired from the researcher's official catalogue. No explicit machine-readable redistribution license is recorded. [Official GPR page](https://www.matteoiacoviello.com/gpr.htm). | Cite the authors/publication and official page. Do not republish the upstream workbook without confirmed permission or license terms. |
| ERA5 single levels | Historical Texas weather | Copernicus CDS catalogue identifies the dataset as CC BY 4.0; DOI `10.24381/cds.adbb2d47`. [ERA5 catalogue](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels). | Redistribution and adaptation require CC BY 4.0 attribution, a license link, and an indication of changes. Preserve dataset DOI, retrieval date, provider/version, request parameters, and provenance. |
| ECMWF SEAS5 and CDS NCEP CFSv2 | Seasonal forecasts and retrospective hindcasts | The CDS seasonal catalogue identifies CC BY 4.0 plus an **Additional licence to use non-European contributions**. Account authentication and license acceptance are required. [Seasonal catalogue](https://cds.climate.copernicus.eu/datasets/seasonal-monthly-single-levels). | Comply with both applicable licenses. Preserve centre, system, initialization, lead, member, issue/availability metadata, retrieval date, and request parameters. Do not treat retrospective hindcasts as archived real-time forecasts. |
| NOAA/NCEI and NOAA CFSv2 | Bounded provider validation and fallback research | U.S. government data is generally reusable, but individual products can contain third-party material and NOAA trademarks are not licensed by data access. [NOAA disclaimer](https://www.noaa.gov/disclaimer). | Attribute NOAA/NCEI or the specific NOAA source and retain provenance. Review product-specific metadata before redistribution. NOAA direct CFSv2 was excluded from EnergyCast bulk acquisition. |
| Census TIGER/Line county boundaries | County geometry used for approximate weather-zone mapping | U.S. Census geographic data is a U.S. government work; product-specific documentation and trademark restrictions still apply. [TIGER/Line documentation](https://www.census.gov/programs-surveys/geography/technical-documentation/complete-technical-documentation/tiger-geo-line.html). | Cite the U.S. Census Bureau, TIGER/Line vintage, retrieval date, and transformations. Do not present the derived ERCOT weather-zone geometry as authoritative. |
| GHCN-h ERCOT-region crosswalk | Approximate county-to-weather-zone mapping | Zenodo DOI `10.5281/zenodo.23653132` declares CC0. | Retain creator, title, DOI, version/date, and note that EnergyCast combines it with Census geometry to create a secondary approximation. |

## Derived and generated artifacts

EnergyCast distinguishes among:

1. **Original project outputs**, such as source code, validation logic, model
   metrics, and narrative reports. These may be covered by the project license
   when they do not reproduce protected third-party content.
2. **Model predictions and aggregate statistics**, whose redistribution may
   still require source attribution depending on their inputs and content.
3. **Transformed source-data extracts**, including normalized or regionalized
   values. Transformation does not automatically remove provider terms.
4. **Raw source files**, which remain entirely subject to their providers'
   terms.

Raw and processed `data/` files and the production bundle are excluded from
Git. Before publishing any generated Parquet, CSV, checkpoint, or downloadable
bundle, classify its contents under the categories above and attach the
applicable source attribution and license information.

## Minimum provenance for a public data artifact

A public EnergyCast data artifact should include:

- provider and dataset title;
- authoritative source URL and DOI when available;
- applicable license name and link;
- retrieval date and provider version/system;
- request parameters or geographic/temporal subset;
- transformations performed by EnergyCast;
- checksum and local artifact version; and
- any provider-required attribution or additional notice.

Credentials, CDS API keys, tokens, and account identifiers must never appear in
source control, manifests, reports, or public metadata.
