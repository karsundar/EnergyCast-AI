# E24 review

The full run completed with 34,344 E24 predictions and 103,032 rows across the
nine-experiment identical-observation comparison. All forecasts are non-oracle.
Positivity, reference-timing and future-label-cutoff violations are zero.

## System-wide results

| Experiment | Energy WAPE | Peak WAPE |
|---|---:|---:|
| E14 | 4.409% | 6.542% |
| E24-B | 4.688% | **6.201%** |
| E25-A | 4.703% | 6.237% |
| E25-B | 4.746% | 6.236% |
| E0 | 4.765% | 6.305% |
| E24-C | 5.829% | 7.411% |
| E24-A | 5.820% | 7.609% |

E24-B, the OOS Holt-Winters-relative log-ratio model with development-selected
shrinkage, is the new operational peak benchmark. Its 6.201% WAPE improves on
E25-B by 0.035 percentage points and E0 by 0.104 points. The paired improvement
over E0 is supported for leads 1-6, while the lead 7-12 interval includes zero.
The improvement over E25-B is small and its confidence intervals include zero
in both horizon bands.

For energy, E24-B improves on E25-A and E0 but remains materially behind E14.
E24-A/C seasonal-reference transformations worsen both targets, demonstrating
that increased extrapolation alone does not guarantee lower error.

E24 models forecast above the historical training maximum more often than the
level-based models, but above-range system forecasts remain negatively biased.
Therefore E24 reduces overall error without eliminating structural
underforecasting. E26 and E27 remain unstarted.
