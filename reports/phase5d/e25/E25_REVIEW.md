# E25 final review

The full run completed 34,344 E25 predictions and 57,240 exact-key comparison
rows across E0, E9 and E25-A/B/C. No oracle rows are present.

## System-wide results

| Experiment | Energy WAPE | Peak WAPE | Energy bias | Peak bias |
|---|---:|---:|---:|---:|
| E25-A climate normals | **4.703%** | 6.237% | -0.829 million MWh | -2,183 MW |
| E25-B SEAS5 | 4.746% | **6.236%** | -0.860 million MWh | -2,219 MW |
| E25-C NCEP | 4.734% | 6.241% | -0.839 million MWh | -2,192 MW |
| E0 Holt-Winters | 4.765% | 6.305% | -0.965 million MWh | -2,409 MW |
| E9 prior residual correction | 4.782% | 6.545% | -0.982 million MWh | -2,537 MW |

Residual correction improves both E0 targets and materially outperforms E9.
E25-A is the energy winner. E25-B's peak advantage over E25-A is only 0.001
WAPE point, so there is no practically important seasonal-weather increment.

For system leads 1-6, E25-A energy WAPE is 4.587%; E25-B is 4.672% and E25-C
4.648%. Peak WAPE is 6.300%, 6.298% and 6.307%. At leads 7-12 all variants
are identical by normal fallback.

Paired bootstrap comparisons show E25's peak improvement over E0 at leads 1-6
is small but its interval excludes zero for all variants. Energy improvements
over E0 are not distinguishable from zero at either horizon band. E25-A/B/C
all improve over E9 at leads 1-6 for energy; E25-A and C intervals exclude
zero, while B narrowly crosses zero.

## Structural bias

System energy bias improves from -0.965 million MWh for E0 to -0.829 million
for E25-A. Peak bias improves from -2,409 MW to -2,183 MW. Residual correction
therefore reduces but does not eliminate underforecasting.

E25 predictions exceed the prior training maximum in roughly 1% of energy and
3-4% of peak rows, versus 9.4% and 12.9% of actuals. This is better than the
zero extrapolation observed for E18-E20 but still does not fully track new
level regimes.

## Decision

Retain E25-A as the best E25 energy configuration and E25-B as the numerical
peak winner, while treating E25-A and E25-B peak performance as effectively
tied. E14 remains the best operational energy benchmark at 4.409%. E25-B peak
at 6.236% modestly improves the previous E0 peak benchmark of 6.305%.

Operational seasonal weather did not add meaningful value beyond normal-based
residual correction. E24, E26 and E27 remain unstarted.
