# Phase 5C Stage 1.5 operational weather readiness

## Status

Stage 1.5 is implemented and bounded validation passed. Stage 2 E18–E23 was not
started. The initial `reports/phase5c/full` and corrected
`reports/phase5c/full_v2` outputs remain unchanged.

Seasonal-compatible ERA5 history covers 2004-01 through 2026-05 for all eight
weather zones plus `SYSTEM` (2,421 rows in 23 yearly partitions). The build
uses UTC calendar months. At each grid cell it first averages hourly U/V,
temperature and dewpoint, then derives vector wind speed and Magnus relative
humidity, then applies area weights. Precipitation and solar energy are summed
within each UTC month before spatial aggregation. Units match the seasonal
products: °C, percent, m/s, mm/month and MJ/m²/month.

## Comparable weather skill

System-wide archived-test MAE over 2024-01 through 2026-05:

| Feature | Expanding normal | Best calibrated provider | Provider MAE | Result |
|---|---:|---|---:|---|
| Temperature (°C) | 1.481 | NCEP | 1.426 | test-only improvement |
| Wind vector speed (m/s) | 0.333 | NCEP | 0.340 | no improvement |
| Relative humidity (%) | 3.933 | SEAS5 | 4.955 | no improvement |
| Precipitation (mm/month) | 20.120 | SEAS5 | 20.789 | no test improvement |
| Solar energy (MJ/m²/month) | 24.945 | SEAS5 | 26.301 | no test improvement |

The comparable definitions resolve the earlier wind mismatch: calibrated NCEP
wind P10–P90 coverage is 0.833, but its point MAE still does not beat normals.
SEAS5 humidity is materially better than NCEP but still worse than normals.

Provider selection is based on archived 2021–2023 development data, not the
2024–2026 test. SEAS5 wins system temperature at every lead 1–6 in development
(MAE 0.970–1.407 versus NCEP 1.448–1.660). Therefore NCEP is not selected merely
because it performs better in the final test. Region/lead development selection
chooses SEAS5 for 48 of 54 combinations and NCEP for six (five NORTH leads and
one FAR_WEST lead). Retrospective hindcast results remain separate research
evidence and never establish historical real-time availability.

Final-test temperature diagnostics are descriptive only. NCEP system MAE is
1.252, 1.403, 1.486, 1.476, 1.472 and 1.469 °C at leads 1–6. SEAS5 is better in
COAST and SOUTHERN and slightly better in winter/spring; NCEP is better in most
other regions and summer/fall. Both degrade for anomalies over 2 °C (NCEP 2.142
°C; SEAS5 2.231 °C).

## Operational eligibility

- **Eligible:** expanding climate normals for all five monthly variables and
  all leads 1–12.
- **Eligible as separate controlled inputs:** calibrated SEAS5 and calibrated
  NCEP monthly temperature at leads 1–6.
- **Eligible with chronological gate:** validation-selected temperature. Each
  origin uses only archived errors observable by that origin; before each
  provider has 12 observations for the region/lead, it falls back to normals.
- **Eligible only for controlled ablation:** calibrated precipitation and solar
  at leads 1–6. They improved over normals during development but not final
  testing, so they must not become default features based on test results.
- **Not selected as dynamic defaults:** wind and relative humidity. Their
  definitions are now comparable, but neither improves on normals.
- **Unavailable:** daily variables, daily HDD/CDD and extreme-weather features.
  None are fabricated from monthly forecasts.

The completed operational table contains 61,344 rows, nine geographies, five
variables and all leads 1–12. Initializations cover 2021-01 through 2026-11 and
targets extend through 2027-10. It has zero missing/nonfinite values, duplicate
business keys, future calibration cutoffs, availability-after-origin errors,
target-month misalignments or dynamic forecasts beyond lead 6. The latest
calibration cutoff precedes its forecast origin by at least 280,800 seconds.

Chronological selected-temperature rows comprise 2,504 SEAS5, 547 NCEP and
4,617 normal fallbacks. The fallback total includes all 3,834 unsupported lead
7–12 rows plus early lead 1–6 origins that lack 12 observable errors per
provider/region/lead. Dynamic selection first becomes eligible for SEAS5 at the
2022-01 initialization and NCEP at 2022-02. Availability remains schedule-based
and is not a file-specific publication audit.

## Commands

Rebuild the versioned compatible ERA5 history without touching canonical data:

```bash
.venv/bin/python scripts/build_phase5c_era5_compatible.py --start-year 2004 --end-year 2026
```

Run the bounded readiness smoke test:

```bash
.venv/bin/python scripts/run_phase5c_stage15.py --smoke-test
```

Regenerate the complete operational feature/audit table when needed:

```bash
.venv/bin/python scripts/run_phase5c_stage15.py \
  2>&1 | tee reports/phase5c_stage15_full_console.log
```

This command has completed successfully. It performs feature construction only;
it does not train a model.
