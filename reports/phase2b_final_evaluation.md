# Phase 2B final evaluation

Generated: 2026-11-15T04:50:13.021520+00:00

## Conclusion

The existing small TFT is a valid leakage-controlled experiment but materially underperforms Holt-Winters. It remains experimental and is not the selected production forecast.

## Confirmed audit findings

- TFT timestamps, horizons, actual values, regional definitions, and units align exactly on the common evaluation keys.
- TFT quantile predictions are returned in original target units through the checkpoint target normalizer; P50 is quantile index 1.
- The original TFT headline used only complete 12-month windows while Phase 2A included shorter end-of-series horizons; the final comparison uses their exact intersection.
- Phase 2A models are refit at every forecast origin; TFT weights remain fixed after training through 2017 while later observed targets are used only as encoder history.
- TFT forecasts have strong negative bias and severely under-cover the nominal P10-P90 interval.
- No target-derived variable is a known decoder input, and future placeholder invariance passed.

## Plausible hypotheses (not confirmed)

- The small capacity and fixed pre-2018 weights may not adapt to the post-2020 level shift as effectively as rolling-refit Holt-Winters.
- Calendar-only covariates and quantile-loss optimization may be insufficient for rare peak and regime-shift behavior.
- The 36-month encoder may capture seasonality but not the longer structural trend; this was not tested by retraining.

## Comparable-observation system results

| Target | Model | N | MAE | RMSE | MAPE % | WAPE % | MASE | Bias | Direction | Coverage |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| peak_demand_mw | holt_winters | 708 | 4230.52 | 5697.64 | 6.051 | 6.042 | 1.139 | -2292.63 | 0.887 | 0.336 |
| peak_demand_mw | seasonal_naive | 708 | 5221.87 | 7330.16 | 7.568 | 7.458 | 1.406 | -2648.60 | 0.835 | 0.551 |
| peak_demand_mw | drift_seasonal_naive | 708 | 6086.29 | 8420.23 | 8.867 | 8.692 | 1.639 | -1210.64 | 0.831 | 0.540 |
| peak_demand_mw | lightgbm_direct | 708 | 7122.54 | 8516.24 | 9.780 | 10.172 | 1.918 | -6113.97 | 0.809 | 0.486 |
| peak_demand_mw | tft_small | 708 | 10301.32 | 12235.58 | 13.857 | 14.712 | 2.774 | -9955.15 | 0.766 | 0.257 |
| total_energy_mwh | holt_winters | 708 | 1734833.38 | 2284756.99 | 4.521 | 4.597 | 1.409 | -954743.83 | 0.867 | 0.381 |
| total_energy_mwh | drift_seasonal_naive | 708 | 2273029.83 | 2913075.66 | 5.987 | 6.024 | 1.846 | -877154.79 | 0.840 | 0.455 |
| total_energy_mwh | seasonal_naive | 708 | 2284703.91 | 2863182.02 | 5.963 | 6.055 | 1.855 | -1820654.25 | 0.831 | 0.278 |
| total_energy_mwh | lightgbm_direct | 708 | 4003842.82 | 4985616.25 | 9.825 | 10.611 | 3.251 | -3931375.07 | 0.818 | 0.617 |
| total_energy_mwh | tft_small | 708 | 6881873.20 | 7808420.63 | 17.478 | 18.238 | 5.588 | -6773216.76 | 0.794 | 0.047 |

## Interpretation and uncertainty

Variable selection and temporal attention charts describe model behavior; they are not evidence of causation.
P10-P90 coverage, quantile loss, interval width, regional/horizon calibration, and crossing checks are stored in the comparable metric and calibration artifacts.

## Future forecast

Holt-Winters is the best validated model. LightGBM is included as a baseline and TFT is explicitly labeled experimental. TFT future-target placeholders were varied from zero to one billion and produced invariant predictions before the forecast artifact was written.
