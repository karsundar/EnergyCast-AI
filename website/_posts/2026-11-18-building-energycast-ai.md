---
title: "Building EnergyCast AI: How ChatGPT Orchestrated an End-to-End Electricity Forecasting Project"
subtitle: "From public energy datasets to reproducible forecasting models—with ChatGPT coordinating the workflow, a coding agent implementing the software, and minimal manual coding."
description: "An evidence-based account of building EnergyCast AI with human direction, ChatGPT orchestration, coding-agent implementation, local AMD hardware, and reproducible validation."
author: Karthik Sundaramoorthy
date: 2026-11-18 09:00:00 -0600
reading_time: 18 min read
excerpt: "Could one person use ChatGPT to coordinate a serious machine-learning engineering project while a coding agent handled implementation? EnergyCast AI became a practical test of that question."
---

Could one person use ChatGPT to coordinate a serious machine-learning engineering project while a coding agent handled implementation? EnergyCast AI became a practical test of that question—one that eventually spanned public-data acquisition, nearly half a billion weather observations, rolling-origin model evaluation, local GPU experiments, and a checksummed production bundle.

The answer was not “press a button and let an autonomous system build everything.” The project worked as an iterative, human-supervised engineering loop. A human set direction and controlled consequential decisions. ChatGPT translated goals and evidence into technical plans. A coding agent implemented, tested, and executed those plans inside the repository. Results flowed back into the next decision.

This is the engineering record of that process.

## The experiment

The initial question was simple:

> Can ChatGPT orchestrate a complex machine-learning engineering project while a coding agent handles the implementation?

Electricity-demand forecasting is a useful stress test. It combines time-series statistics, machine learning, large geospatial datasets, operational data availability, and strict leakage controls. A plausible forecast is easy to generate; a forecast that can survive a chronological backtest and a production-readiness audit is much harder.

EnergyCast AI focused on monthly ERCOT electricity consumption and peak demand across eight weather zones and the ERCOT system. It deliberately kept ERCOT grid demand separate from EIA retail electricity sales, used rolling forecast origins, and distinguished observed-future-weather “oracle” experiments from forecasts that could have been issued operationally.

The project advanced phase by phase. Every phase had a defined stop condition, validation evidence, and explicit restrictions. Failed approaches were retained as evidence rather than edited out of the story.

## Development architecture

Three participants had different responsibilities.

**The human** defined the research objectives, supplied the local infrastructure, reviewed evidence, approved decisions, accepted data licences, and performed manual acquisition when public-data systems required browser interaction.

**ChatGPT** acted as the technical orchestrator: decomposing work, designing experiments, reviewing results, identifying leakage risks, interpreting failures, and deciding what evidence was needed before the next phase.

**The coding agent** worked inside the repository: implementing Python pipelines, executing bounded tests, training approved models, producing reports, and building guarded production infrastructure.

<figure class="diagram">
  <img src="{{ '/assets/images/orchestration.svg' | relative_url }}" alt="Workflow from human direction to ChatGPT orchestration, coding-agent implementation, local execution, results, and back to ChatGPT and the human.">
</figure>
<p class="figure-caption">The loop was iterative and supervised. ChatGPT coordinated the technical process; it did not operate independently of human review.</p>

The boundaries mattered. ChatGPT could recommend a full historical acquisition, but the human decided when to start a long-running job. The coding agent could implement a production scheduler, but it could not activate unattended publishing without approval. When a model underperformed, the evidence—not model novelty—determined what happened next.

## Manual versus automated work

Most implementation code was generated and executed through the coding agent. That included acquisition handlers, schema validation, geospatial normalization, rolling backtests, calibration experiments, test suites, and production safeguards. ChatGPT maintained the research thread across those components and turned results into the next set of controlled experiments.

Manual intervention remained essential in two places.

First, some government and public-data workflows imposed source-specific constraints. Copernicus required account configuration and licence acceptance. CDS jobs entered long queues, occasionally returned interrupted downloads, and sometimes produced archives whose response format differed from the requested file extension. Several bounded files were downloaded through the browser and then handed to the validation pipeline. These were not model-development shortcuts; they were practical responses to access controls and service behavior.

Second, human approval was required for material choices: changing dependencies, launching overnight acquisitions, selecting experiment gates, stopping expensive training, freezing production candidates, and deciding whether publication or scheduling could be enabled.

No percentage of “automation” is claimed. The work does not divide cleanly that way: an automatically generated downloader still depended on human-approved scope, and a manually downloaded file still passed automated signature, schema, checksum, and coverage checks.

| Work area | Human | ChatGPT | Coding agent |
|---|---|---|---|
| Project scope | Defines objectives and constraints | Converts goals into phased plans | Implements the approved scope |
| Architecture | Approves consequential choices | Designs components and interfaces | Builds and tests them |
| Data access | Accepts licences; handles required browser downloads | Identifies authoritative sources and risks | Automates discovery, validation, caching, and normalization |
| Experiments | Authorizes expensive runs | Designs comparisons and interprets evidence | Implements training and evaluation |
| Operations | Controls publication and scheduling | Defines release gates and failure policy | Implements checks, history, rollback, and logs |
| Quality control | Reviews outputs and resolves decisions | Audits claims, leakage, and comparability | Runs deterministic tests and generates reports |

## Local infrastructure

All experiments ran locally—without rented cloud GPU infrastructure—on an Ubuntu Linux workstation with:

- an **AMD Radeon AI PRO R9700** with 32 GB of VRAM;
- **64 GB of system RAM**;
- **ROCm** and a compatible **PyTorch** build; and
- CPU execution for Statsmodels and standard LightGBM workloads.

PyTorch on ROCm exposes its accelerator through the CUDA-compatible Python interface, so GPU checks used `torch.cuda.is_available()` alongside `torch.version.hip`. The environment was validated with matrix multiplication and neural-network forward/backward operations before any model training.

Local execution created its own engineering constraints. TFT training initially failed because a ROCm backward operation did not have a deterministic implementation. Later, daily TFT work was bottlenecked by thousands of batches per epoch. DataLoader workers, pinned memory, prefetching, batch size, and BF16 were benchmarked independently rather than changed all at once. The resulting runs could saturate the GPU, but better utilization did not make the model more accurate.

## Data engineering

The largest part of EnergyCast was not model code. It was making heterogeneous public data comparable, reproducible, and safe to use at a historical forecast origin.

| Source | Validated scale | Role |
|---|---:|---|
| ERCOT historical demand | ~1.60 million hourly regional records | Energy and peak targets |
| EIA electricity data | 124,440 monthly records | Separate retail-sales dataset |
| ERA5 | 466,855,488 hourly grid observations | Historical Texas weather |
| ECMWF SEAS5 | 6,949,404 normalized records | Seasonal forecasts and hindcasts |
| NCEP CFSv2 via CDS | 11,483,472 normalized records | Secondary seasonal provider |

ERCOT source files changed format over time and crossed daylight-saving transitions. The pipeline preserved both local and UTC timestamps, calculated energy from demand and interval duration, retained an incomplete 2016 interval instead of fabricating it, and built system peaks from simultaneous regional demand rather than summing noncoincident regional peaks.

ERA5 required a different scale of design. Texas was extracted on a 0.25-degree grid, processed month by month, and stored as compressed partitions rather than loaded as a single in-memory table. Hourly observations were converted into daily and monthly features while respecting local-day boundaries. Temperature, dewpoint, U/V wind, precipitation, and solar radiation each required unit and accumulation semantics to be verified. Degree days were calculated at the grid-cell level before spatial aggregation.

The eight ERCOT weather zones added a geospatial provenance problem. No authoritative machine-readable zone boundary was available in the acquired sources, so the project used a published county crosswalk with Census county geometry and labeled the result as an approximation. Equal-area cell intersections created a reusable spatial weight matrix, including partial and coastal grid cells. Inferred boundaries were never presented as official ERCOT GIS data.

Seasonal weather introduced time-of-availability constraints. Archived forecasts and retrospective hindcasts were evaluated separately. Hindcasts did not receive invented historical release timestamps. The 2017–2020 archive gap was left open. Leads beyond the supported seasonal horizon used expanding climate normals built only from information available at the forecast origin.

<figure class="diagram">
  <img src="{{ '/assets/images/architecture.svg' | relative_url }}" alt="EnergyCast architecture from official electricity and weather sources through validated caches, canonical Parquet, leakage-safe features, rolling evaluation, frozen models, and guarded production.">
</figure>
<p class="figure-caption">Every transition preserves provenance, availability metadata, and validation evidence.</p>

Downloaders used temporary files, response signatures, parseability checks, SHA-256 hashes, atomic moves, retries, request fingerprints, and resumable ledgers. An HTTP 200 response containing HTML was treated as an error, not a dataset. This kind of work rarely appears in a model leaderboard, but it determines whether the leaderboard means anything.

## Forecasting research

The research started with statistical baselines and expanded only when a specific hypothesis justified it.

**Holt-Winters** provided a strong seasonal reference. **SARIMA** and **SARIMAX** tested whether statistical trend and weather regressors added value. **LightGBM** tested direct multi-horizon relationships with calendar and lagged features. **Temporal Fusion Transformer** and **Chronos-2** tested neural and foundation-model approaches. Later phases added residual correction, log-ratio targets, and validation-weighted ensembles.

The evaluation was chronological. Forecast origins advanced month by month, horizons ran from one to twelve months, and training data stopped at each origin. Known-future calendar fields were separated from historical observations. Intermediate future demand never entered multi-step feature construction. Weather using observed future ERA5 values was explicitly marked as an oracle research experiment, not operational accuracy.

## Amazon Chronos-2: The Time-Series Foundation Model

EnergyCast evaluated **Amazon Chronos-2** as its pretrained time-series foundation model. The hypothesis was not that a foundation model should replace the forecasting stack. It was that a model pretrained across diverse time-series patterns might contribute a useful forecast signal—especially where a single ERCOT series provides limited examples of long-term growth, shocks, and regime changes.

That makes Chronos-2 different from the other model families in the project. Holt-Winters estimates level, trend, and seasonal structure directly from each EnergyCast history. SARIMA fits explicit autoregressive, moving-average, and seasonal relationships. LightGBM learns supervised relationships from engineered lags, calendar fields, baseline forecasts, and residual or log-ratio targets. Chronos-2 arrives with representations learned during large-scale pretraining and performs probabilistic sequence forecasting from the supplied context.

The production E14 energy ensemble uses **E11, the zero-shot Chronos-2 experiment**—not the separately evaluated E13 LoRA fine-tuning experiment. Validation-only weights combine four weather-independent components:

| E14 component | Production role | Frozen energy weight |
|---|---|---:|
| E0 | Holt-Winters | 0.014669 |
| E8 | SARIMA without weather | 0.062276 |
| E9 | Holt-Winters + LightGBM residual correction | 0.110263 |
| E11 | Amazon Chronos-2 zero-shot | 0.812792 |

The large Chronos-2 weight shows that its signal was valuable on the development period; it does not mean Chronos-2 is the whole system. E14 still requires all four components, and its weights were learned on validation forecasts rather than the final test period. The peak path is separate: **E24-B uses Holt-Winters with a LightGBM log-ratio correction** and a frozen multiplicative shrinkage factor.

<figure class="diagram">
  <img src="{{ '/assets/images/model-architecture.svg' | relative_url }}" alt="Energy model path combining Holt-Winters, SARIMA, LightGBM residual correction, and zero-shot Amazon Chronos-2 into the E14 ensemble; peak model path combining Holt-Winters and LightGBM log-ratio correction into E24-B.">
</figure>
<p class="figure-caption">The E14 weights are copied from the frozen production manifest. Chronos-2 is one weighted component of the energy architecture; peak forecasting does not use it.</p>

For reproducibility, the frozen bundle vendors the Chronos-2 checkpoint and pins Amazon's `amazon/chronos-2` model to revision **`ff35bf3168857dce1984939b85d8955976f12f32`**. Inference is local, uses BF16 through PyTorch, and refuses CPU fallback: the checkpoint runs on the AMD Radeon AI PRO R9700 through ROCm's CUDA-compatible Python interface. The hybrid architecture therefore combines a locally executed foundation model with fitted statistical and gradient-boosted components, rather than depending on an external model API or on Chronos-2 alone.

### Complexity did not guarantee improvement

The small TFT was successfully trained on the AMD GPU, produced finite quantiles, saved and reloaded checkpoints, and passed leakage checks. It still materially underperformed Holt-Winters. A diagnostic audit confirmed that inverse scaling, timestamp alignment, P50 selection, units, and metric aggregation were correct. The underperformance was real.

Weather-aware level-based LightGBM models exposed another failure mode. As demand grew beyond the training range, tree predictions never exceeded the historical training maximum. Above-range actual demand occurred in 12.9% of peak forecasts and 9.4% of energy forecasts; every above-range system forecast underpredicted. The models had no explicit clipping. This was a structural extrapolation limitation.

That diagnosis redirected the work toward models that preserved strong baselines and learned corrections:

- **Holt-Winters + LightGBM residual correction** learned from historical out-of-sample residuals, never merely in-sample fitted errors.
- **Holt-Winters log-ratio LightGBM** predicted a multiplicative correction and reconstructed demand in original units.
- **Validation-weighted ensembling** combined complementary non-oracle forecasts with weights learned only on development data.

Those hybrids were less fashionable than a large deep network and more effective for this dataset.

## Validated results

The final operational candidates were selected with chronological development criteria and evaluated on identical historical observations.

| Target | Model | System WAPE |
|---|---|---:|
| Monthly energy | **E14 non-oracle ensemble** | **4.409%** |
| Monthly energy baseline | Holt-Winters | 4.765% |
| Monthly peak | **E24-B log-ratio model** | **6.201%** |
| Monthly peak baseline | Holt-Winters | 6.305% |

<figure class="diagram">
  <img src="{{ '/assets/images/model-comparison.svg' | relative_url }}" alt="Bar chart comparing validated WAPE for selected operational models and Holt-Winters baselines for energy and peak demand.">
</figure>
<p class="figure-caption">Lower is better. Values are system-wide historical rolling-origin backtests on identical observations.</p>

E14 combined weather-independent forecasts and improved monthly energy WAPE by about 0.36 percentage points over Holt-Winters. E24-B improved peak WAPE by about 0.10 percentage points. E25-B, a seasonal-weather residual model, reached 6.236% peak WAPE but did not conclusively beat E24-B under the horizon-band bootstrap analysis.

These are **historical backtest results**, not live performance guarantees. They are also not evidence of superiority over official ERCOT forecasts; no matching official benchmark was evaluated.

Observed-future-weather oracle experiments were useful for measuring the value of perfect weather information, but they remained separate from operational claims. Raw seasonal weather provided only modest changes and could not repair the structural underforecasting of level-based tree models.

## Probabilistic calibration

The first frozen P10–P90 calibration failed its 80% coverage target. System coverage was 53.5% for energy and 57.7% for peak; regional coverage was 69.1% and 65.4%. The deterministic models remained valid, but the intervals were not suitable for publication.

Crucially, the failed final-test result was not used to inflate intervals and claim success. Instead, a separate research remediation compared scale-normalized conformal and asymmetric residual-quantile methods using four development-only blocked folds. It used zero final-test rows and produced:

- 80.5% energy-system coverage;
- 74.2% energy-regional coverage;
- 91.9% peak-system coverage; and
- 80.8% peak-regional coverage.

Coverage was still not uniformly reliable, especially during high-growth conditions, and no independent historical holdout remained. The candidate calibrators were frozen for prospective shadow evaluation, while public P10/P90 intervals stayed disabled.

That outcome is a useful distinction between engineering and presentation: a chart can always be made wider, but a probability claim requires evidence it did not train on.

## Production engineering

The deterministic V1 design turns two selected research recipes into a strictly validated inference bundle named **`energycast_v1_202610`**.

Each run produces **216 forecasts**:

```text
2 targets × 9 geographies × 12 monthly horizons = 216 forecasts
```

The nine geographies are the eight ERCOT weather zones plus the ERCOT system. Outputs carry an issuance timestamp, data cutoff, source vintage, model version, and historical accuracy context.

The frozen bundle contains 27 fitted statistical states, two LightGBM models, E14 ensemble weights, preprocessing and feature schemas, inference inputs, and a pinned local Chronos-2 checkpoint reference. Ten artifacts totaling 503,484,976 bytes are covered by SHA-256 checksums. The production runner rejects missing, corrupt, or mismatched components rather than silently refitting.

Two fresh processes loaded the bundle and each generated 216 finite forecasts with a maximum difference of exactly 0.0. A simulated failure preserved the previous valid output checksum. The design also includes:

- data freshness and nine-geography completeness checks;
- cutoff and source-vintage validation;
- immutable forecast history and deterministic forecast IDs;
- atomic publication and manual rollback;
- actual-versus-forecast monitoring;
- structured logs and failure recovery;
- a scheduler preview that does not install itself; and
- shadow calibration fields kept out of public output.

At the documented milestone, the bundle was validated, but **no live forecast had been published and unattended scheduling had not been activated**. Production readiness was treated as a set of gates, not permission to deploy.

## Lessons learned

### AI orchestration is different from code generation

Generating a Python function is the easy part. The larger contribution was maintaining an evidence-driven chain: acquisition constraints informed normalization; normalization decisions informed feature availability; backtest failures motivated diagnostics; diagnostics constrained the next experiment; and validation results controlled production gates.

### Human oversight remained structural

The human was not a passive requester. Licence acceptance, infrastructure access, experiment authorization, long-running job control, and release decisions all required judgment and accountability. AI expanded the amount of work one person could coordinate; it did not remove ownership.

### Data engineering dominated the risk

Timestamp boundaries, accumulated weather variables, response formats, changing ERCOT schemas, approximate zone geography, and historical availability were more dangerous than choosing the wrong model class. Checksums and cutoff audits mattered as much as hyperparameters.

### Statistical baselines deserved respect

Holt-Winters remained difficult to beat. TFT and direct LightGBM experiments were valuable because their failure exposed real structure: limited sample size, demand growth, extrapolation ceilings, and the difference between fitting patterns and projecting levels.

### Reproducibility needs product engineering

A metric in a notebook is not a deployable forecast. Frozen artifacts, dependency records, immutable history, checksum validation, failure recovery, and non-publishing dry runs were necessary to bridge research and operation.

## Conclusion

EnergyCast AI demonstrates a practical form of AI-assisted engineering: a human can coordinate substantially more technical work when ChatGPT maintains the research architecture and a coding agent performs disciplined implementation inside a real repository.

The most important result is not that the system used AI. It is that the process preserved the habits serious forecasting requires—authoritative sources, chronological evaluation, explicit uncertainty, reproducible artifacts, honest failures, and release controls.

That is a more useful vision than exaggerated autonomy: not an unsupervised machine replacing the engineer, but an orchestration loop that lets one person direct a much broader engineering program while retaining review, evidence, and responsibility.

---

The implementation, phase reports, and validation evidence are available in the [EnergyCast AI repository](https://github.com/karsundar/EnergyCast-AI).
