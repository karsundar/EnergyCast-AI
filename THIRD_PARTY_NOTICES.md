# Third-party notices

EnergyCast AI is licensed under the Apache License 2.0. That license applies
to original EnergyCast source code and documentation. It does **not** replace
or extend the licenses of third-party software, models, datasets, trademarks,
or other materials.

This file is an attribution summary, not legal advice or a substitute for the
complete upstream license texts. A distributor is responsible for reviewing
the exact artifacts it ships and preserving all applicable notices.

## Amazon Chronos-2

EnergyCast uses Amazon Chronos-2 as one component of the E14 energy ensemble.

- Upstream project: <https://github.com/amazon-science/chronos-forecasting>
- Model: <https://huggingface.co/amazon/chronos-2>
- License: Apache License 2.0
- Production revision:
  `ff35bf3168857dce1984939b85d8955976f12f32`
- EnergyCast usage: E11 zero-shot inference inside the E14 ensemble

The production bundle may contain an unmodified local snapshot of the upstream
model configuration and weights. The snapshot remains third-party material
under its upstream license; it is not relicensed as original EnergyCast code.

Before distributing a bundle containing Chronos-2, include at minimum:

1. the EnergyCast `LICENSE` and this notice;
2. the complete upstream Chronos-2/Chronos Forecasting Apache 2.0 license and
   any upstream `NOTICE` supplied with the distributed revision;
3. the upstream model card, source URL, pinned revision, and citation; and
4. a manifest identifying which files are unmodified upstream artifacts.

The local `models/production/energycast_v1_202610/` bundle is excluded from
Git. Its presence in a developer checkout does not mean it is approved for
public redistribution. The tracked E13 adapter checkpoints are derivative
model artifacts and must retain the Chronos-2 provenance and license notice.

## Software dependencies

EnergyCast environments are assembled from independently licensed packages.
The principal direct dependencies include:

| Component | Typical upstream license | Role |
|---|---|---|
| PyTorch | BSD-style | Neural and foundation-model execution |
| Lightning | Apache 2.0 | TFT training orchestration |
| PyTorch Forecasting | MIT | TFT implementation |
| Chronos Forecasting | Apache 2.0 | Chronos-2 inference/fine-tuning experiments |
| Hugging Face Transformers | Apache 2.0 | Model loading and execution |
| LightGBM | MIT | Gradient-boosted forecasting and corrections |
| Statsmodels | BSD-3-Clause | Statistical forecasting |
| pandas | BSD-3-Clause | Tabular processing |
| NumPy | BSD-3-Clause | Numerical processing |
| SciPy | BSD-3-Clause | Scientific computing |
| scikit-learn | BSD-3-Clause | Metrics and preprocessing |
| PyArrow | Apache 2.0 | Parquet storage |
| DuckDB | MIT | Partitioned analytical processing |
| xarray | Apache 2.0 | Weather-array processing |
| GeoPandas | BSD-3-Clause | Geospatial processing |
| Shapely | BSD-3-Clause | Geometry operations |
| Jekyll and website plugins | Primarily MIT | Technical blog generation |

The authoritative versions are the repository requirement and lock files. A
packaged application, container, environment, or offline bundle should include
the complete license texts and notices for the exact resolved versions; this
summary alone is not sufficient for binary redistribution.

## Data and geographic materials

Dataset-specific attribution, access, and redistribution information is kept
in `DATA_LICENSES.md`. Data-provider terms remain controlling even when data is
downloaded by EnergyCast scripts or transformed into Parquet.

Provider names and trademarks—including ERCOT, EIA, ECMWF, Copernicus, NOAA,
NCEP, the Federal Reserve Bank of St. Louis, the Federal Reserve Bank of New
York, Hugging Face, and Amazon—belong to their respective owners. Their mention
describes provenance and does not imply endorsement.
