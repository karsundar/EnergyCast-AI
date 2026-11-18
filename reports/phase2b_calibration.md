# Phase 2B calibration

P10-P90 is nominally 80%. Measured coverage, quantile loss, raw and normalized interval width, and quantile crossing are reported overall and by horizon, region, and region/horizon in `phase2b_calibration.csv`. No quantile crossing was observed. Final test residuals were not used to recalibrate forecasts.

## Overall TFT calibration

| Target | N | Coverage | Mean width | Normalized width | Crossing | P10 loss | P50 loss | P90 loss |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| peak_demand_mw | 6372 | 0.2819 | 1991.817 | 0.1261 | 0.0000 | 343.290 | 1279.575 | 1479.330 |
| total_energy_mwh | 6372 | 0.1566 | 745078.373 | 0.0889 | 0.0000 | 195859.264 | 784297.053 | 1118432.960 |
