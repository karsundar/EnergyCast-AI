# Phase 2A baseline evaluation

Generated: 2026-11-14T21:13:41.219066+00:00

## Scope and leakage controls

- ERCOT monthly energy and peak targets cover all eight weather zones plus the simultaneous regional system sum.
- Periods below 99.0% completeness are excluded; incomplete observed periods remain explicitly flagged.
- Calendar features are known-future inputs. Demand lags, trailing statistics, growth, and peak features use only data available at each forecast origin.
- All models produce 12-month paths without consuming actual intermediate future demand.
- LightGBM candidates were selected on 2018–2019 only. Empirical intervals use 2020 residuals only. Test results begin in 2021.
- Economic and geopolitical feature files are prepared separately and excluded from all baseline fits.

## ERCOT system test summary

| Target | Model | N | MAE | RMSE | WAPE % | MASE | Bias | Direction accuracy | P10–P90 coverage |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| peak_demand_mw | holt_winters | 774 | 4168.846 | 5578.195 | 5.867 | 1.123 | -2367.433 | 0.885 | 0.324 |
| peak_demand_mw | seasonal_naive | 774 | 5304.587 | 7282.158 | 7.466 | 1.429 | -2847.459 | 0.823 | 0.516 |
| peak_demand_mw | drift_seasonal_naive | 774 | 6068.148 | 8291.139 | 8.541 | 1.634 | -1489.134 | 0.819 | 0.519 |
| peak_demand_mw | lightgbm_direct | 774 | 7250.184 | 8614.917 | 10.204 | 1.952 | -6324.926 | 0.805 | 0.472 |
| total_energy_mwh | holt_winters | 774 | 1787192.297 | 2357766.885 | 4.654 | 1.451 | -1041919.623 | 0.872 | 0.367 |
| total_energy_mwh | drift_seasonal_naive | 774 | 2305004.040 | 2947363.803 | 6.003 | 1.872 | -995397.066 | 0.845 | 0.447 |
| total_energy_mwh | seasonal_naive | 774 | 2339877.198 | 2932419.413 | 6.094 | 1.900 | -1899885.755 | 0.833 | 0.279 |
| total_energy_mwh | lightgbm_direct | 774 | 4107818.353 | 5106221.086 | 10.698 | 3.336 | -4019394.344 | 0.820 | 0.606 |

## Data-quality and evaluation notes

- Prediction statuses: {'complete': 55728, 'incomplete_horizon': 5616}.
- The 2016-11 missing interval remains in the targets and is flagged according to the configurable completeness threshold; no observation is synthesized.
- The source 2021 ERCOT-total discrepancy is preserved. System targets are always built from simultaneous regional sums.
- COVID breakdown covers the portion of March 2020–June 2021 present in the test set. Extreme-period results use a load-based proxy because Copernicus remains disabled.
- Full zone/horizon/season, COVID, extreme-load, interval, quantile-loss, and incomplete-horizon results are in the metrics and prediction artifacts.
