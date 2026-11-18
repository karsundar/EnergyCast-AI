# V1 calibration remedy review

The full development-only evaluation completed across four forecast-origin
blocks with 11,340 candidate predictions. Inputs end in December 2020; no
final-test rows were read and no calibration cutoff crossed an evaluation
origin.

## Selected development methods

| Target | Scope | Method | Coverage | Normalized pinball loss |
|---|---|---|---:|---:|
| Energy | System | Relative asymmetric | 80.5% | 0.0101 |
| Energy | Regional | Relative asymmetric | 74.2% | 0.0192 |
| Peak | System | Relative asymmetric | 91.9% | 0.0147 |
| Peak | Regional | Scale-normalized asymmetric | 80.8% | 0.0318 |

Energy-system and peak-regional coverage are close to the nominal 80% target.
Energy-regional intervals remain undercovered and peak-system intervals are
over-wide. Coverage also varies materially across forecast-origin blocks and
horizon bands.

High-growth conditional coverage is especially weak. Several system
high-growth blocks contain very small samples and zero covered observations;
regional high-growth coverage also falls well below normal-growth coverage.
This is consistent with the previously identified structural-growth regime
shift and prevents a deployment claim.

The methods and frozen calibration tables are retained as prospective research
candidates. No untouched historical holdout remains after remedy development.
Probabilistic intervals therefore remain disabled until prospective or
independently held-out confirmation. Deterministic E14 energy and E24-B peak
forecasts may proceed unchanged. E26 and E27 remain unstarted.
