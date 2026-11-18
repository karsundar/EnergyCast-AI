# Phase 5D structural-bias investigation

## Scope

This is a read-only diagnostic of 34,344 saved E18-E20 predictions. No model
was modified, fitted or selected. All comparisons remain non-oracle.

## Confirmed findings

- The models predict absolute demand levels with a global direct multi-horizon
  LightGBM objective. They are not recursive, so recursive error accumulation
  is not the cause. There is no explicit prediction clipping or target
  normalization in the implementation.
- System actuals exceed their geography-specific historical training maximum
  in 12.9% of peak forecasts and 9.4% of energy forecasts. Model predictions
  exceed that historical maximum in 0% of cases.
- When actual demand exceeds the historical maximum, E18 mean bias worsens to
  -11,824 MW peak and -8.723 million MWh energy, compared with -6,569 MW and
  -3.941 million MWh inside the historical range. Every above-range system
  forecast underpredicts.
- Actual-to-training-maximum ratio is strongly associated with error: Pearson
  correlation is -0.693 for peak and -0.846 for energy. Correlation with
  absolute error is 0.598 and 0.846, respectively.
- High-growth regimes are harder. E18 system bias for growth over 6% is
  -9,667 MW peak and -5.125 million MWh energy, versus -4,971 MW and
  -3.295 million MWh in negative-growth periods.
- Longer horizons worsen E18 system WAPE: peak rises from 11.05% at leads 1-6
  to 11.83% at leads 7-12; energy rises from 11.10% to 12.55%.
- Bias intensifies after 2021. In 2025, system E18 bias is -9,519 MW peak and
  -5.724 million MWh energy. The distribution shift is therefore structural,
  not primarily a weather-provider effect.
- SEAS5 has a measurable but small lead-1-6 benefit. Against E18, system WAPE
  changes by -0.503 percentage points for energy (95% bootstrap interval
  -0.654 to -0.354) and -0.282 for peak (-0.494 to -0.066). NCEP worsens both:
  +0.151 energy (+0.034 to +0.261) and +1.188 peak (+0.925 to +1.462).
- Leads 7-12 are identical for E18-E20 by design. Weather changes cannot repair
  the level-extrapolation limitation at these horizons.

## Interpretation

The primary confirmed mechanism is level-target extrapolation failure under a
shifting demand distribution. Trend, lags and weather can select among learned
tree leaves, but the fitted level model does not extrapolate sufficiently into
new demand regimes. Weather sensitivity exists, particularly for SEAS5, but is
small relative to the level bias. Residual correction and growth targets are
therefore better-matched hypotheses than additional tuning of the same level
model.

## Operational issuance

Across all 53 initializations, neither provider is available at month start.
Both are available for the defined day-15 refresh. Month-start forecasts must
use expanding normals. Mid-month forecasts may use provider weather, while
retaining the scheduled-release-not-file-audit caveat.

## Recommended priority

1. **E25:** Holt-Winters plus strictly out-of-sample seasonal-weather residual
   correction. It starts from the strongest relevant baseline and directly
   tests bias reduction with a no-correction control.
2. **E24:** Growth/log-ratio LightGBM targets selected on development origins.
   This directly tests the observed extrapolation limitation.
3. **E27:** Operational-weather SARIMAX with a weather-independent control.
4. **E26:** Chronos-2 future-covariate experiment. The installed Chronos-2
   implementation supports known future covariates, but GPU execution and input
   construction require a separate smoke gate.

No E21-E27 experiment has started.
