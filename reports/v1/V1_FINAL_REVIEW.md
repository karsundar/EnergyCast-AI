# EnergyCast V1 final validation review

## Model selection

Selection used only 2018-2019 development observations. E14 was selected for
energy at 2.969% development WAPE; E24-B was selected for peak at 4.320%.
The frozen 2021+ evaluation confirms 4.409% energy WAPE and 6.201% peak WAPE.

Paired final-test bootstrap comparisons support E14 energy over E0 by 0.356
WAPE points (95% interval -0.519 to -0.192) and E24-B peak over E0 by 0.104
points (-0.168 to -0.038). These final results did not influence selection.

## Probabilistic calibration

Relative symmetric conformal scaling was selected using a Jan-Jun 2020 fit and
Jul-Dec 2020 holdout, then refit on all reserved 2020 OOS residuals. It does not
pool unscaled regional errors and uses target/geography/horizon groups with
same-geography fallback.

Final P10-P90 empirical coverage is below the nominal 80% target:

| Target | System | Regional |
|---|---:|---:|
| Energy | 53.5% | 69.1% |
| Peak | 57.7% | 65.4% |

This is evidence of post-2020 residual-distribution shift, consistent with the
documented structural demand growth. Intervals must not be widened using final
test outcomes. Probabilistic deployment is therefore blocked pending a new
pre-test calibration design or additional historically available calibration
evidence.

## Operational audit

- 11,448 selected-model forecasts were evaluated.
- No oracle-weather rows, quantile crossings, late weather, or final-test
  calibration rows were found.
- Month-start and day-15 schedules are valid but identical for frozen V1
  candidates; no refresh benefit is claimed.
- The latest-origin output contains horizons 1-12 for every target/geography.
- E26 and E27 remain unstarted.

Deterministic V1 selection is validated. Deployment work must remain stopped
until the probabilistic undercoverage decision is reviewed.
