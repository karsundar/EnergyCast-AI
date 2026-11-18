# Phase 5C Stage 1 review

## Outcome

The full evaluation completed on 23,598 exact provider/ERA5 matches. Structural
validation passed: both providers cover leads 1–6; archived forecasts cover
2021-01 through 2026-05; retrospective hindcasts cover 2004-01 through 2017-05;
archived availability is complete; hindcast availability remains null; and no
archived forecasts were fabricated for 2017–2020.

The first full output is preserved under `reports/phase5c/full`. Its wind
comparison used a spatially inconsistent aggregation. Versioned Phase 5C inputs
now average grid-cell scalar wind before ensemble summarization and leave every
Phase 5B artifact unchanged. The corrected evaluation is under
`reports/phase5c/full_v2`.

## Corrected archived-test results

System-wide results below pool leads 1–6 over 2024-01 through 2026-05. Values
are MAE in each feature's canonical unit.

| Feature | Expanding normal | Best calibrated dynamic forecast | Dynamic MAE | Change from normal |
|---|---:|---|---:|---:|
| Temperature (°C) | 1.478 | NCEP | 1.423 | -3.7% |
| Precipitation (mm/month) | 20.145 | SEAS5 | 20.792 | +3.2% |
| Solar energy (MJ/m²/month) | 24.892 | SEAS5 | 26.284 | +5.6% |
| Relative humidity (%) | 3.780 | SEAS5 | 4.668 | +23.5% |
| Wind speed (m/s) | 0.181 | NCEP | 0.807 | +345.8% |

Only calibrated NCEP temperature improves on the expanding normal. The
validation-weighted ensemble is not uniformly best. Its temperature MAE is
1.440 °C; its other system MAEs are 21.157 mm precipitation, 30.029 MJ/m²
solar, 5.160 percentage points humidity and 0.863 m/s wind.

Temperature ensemble MAE rises from 1.154 °C at lead 1 to about 1.47–1.52 °C
at leads 2–6. Other variables show the same general lead degradation. Raw and
calibrated quantiles have zero crossing, but reliability is mixed. Corrected
system P10–P90 coverage is 0.684 for the temperature ensemble, 0.782 for
precipitation, 0.718 for solar, 0.695 for humidity and 0.218 for wind, versus
the nominal 0.80 target.

## Confirmed limitations

- `available_at` is the provider's documented scheduled operational release,
  not a file-specific archived publication audit timestamp. Results are a
  schedule-based operational simulation, not a strict audited real-time replay.
- Seasonal wind uses monthly-mean U/V. Even after correcting spatial
  aggregation, it cannot equal ERA5's mean hourly scalar speed. Wind skill is a
  proxy comparison and must not select an operational feature without building
  a seasonal-compatible ERA5 vector-wind history.
- Seasonal relative humidity is derived from monthly-mean temperature and
  dewpoint; ERA5 canonical humidity averages hourly values. This nonlinear
  difference makes humidity skill proxy-only.
- The expanding normal has zero forecast anomaly by construction, so anomaly
  correlation is undefined for that baseline. Dynamic anomaly correlations are
  weak in the archived test.
- Regional geometry remains the documented approximate ERCOT mapping.

## Stage 2 gate

Do not launch E18–E23 yet. A defensible initial Stage 2 design should use NCEP
temperature where seasonal data exist and expanding normals for unsupported
leads. Precipitation and solar should retain climate normals unless electricity
validation demonstrates incremental value using validation data only. Wind and
humidity require seasonal-compatible historical transformations or exclusion.
Prediction intervals need validation-only scale-aware recalibration because
raw provider coverage is not reliably 80%.
