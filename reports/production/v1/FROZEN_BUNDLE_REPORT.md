# EnergyCast V1 frozen fitted-model bundle

Status: validated, not published.

## Identity and cutoff

- Bundle: `energycast-v1.0.0-frozen-202610`
- Approved demand/context cutoff: `2026-10-01`
- Forecast issuance: `2026-11-02T12:15:00Z`
- Forecast horizon: 12 months
- Random seed: 42
- Size: 503,484,976 bytes across 10 checksummed artifacts (481 MiB on disk)

Component label cutoffs are recorded independently:

- Holt-Winters, SARIMA and Chronos context: 2026-10-01
- E9 preserved OOS residual target month: 2021-12-01
- E24-B latest observable label timestamp: 2026-05-01
- Expanding-normal weather availability timestamp: 2026-11-01T00:00:00Z

All four cutoffs precede the approved 2026-11-02T12:15:00Z issuance.

## Contents

- 9 fitted Holt-Winters energy states used by E0/E9.
- 9 fitted SARIMA energy states used by E8.
- 9 fitted Holt-Winters peak states used by E24-B.
- Frozen E9 energy residual LightGBM model and feature schema.
- Frozen E24-B peak log-ratio LightGBM model, feature schema and 0.25 shrinkage.
- E14 validation-only weights for E0/E8/E9/E11.
- Vendored Chronos-2 revision `ff35bf3168857dce1984939b85d8955976f12f32`.
- Frozen demand context, statistical reference forecasts and inference features.
- Dependency versions, source checksums, hyperparameters and transformations.

## Artifact checksums

| Artifact | SHA-256 |
|---|---|
| `chronos2/config.json` | `ef1143bfdc9c0376d9a056eefca46cb4b1ec3d0ffacd541ff56feb40fb708031` |
| `chronos2/model.safetensors` | `ddcda3c7508bf2528087723e98a20707cc04b7f370ae275a9fd88078ddba4f42` |
| `e24b_inference_features.parquet` | `673e446cda409a2a8d43a77abccca760e6a5697ea844735c6fac7ef0432a2114` |
| `e24b_peak_log_ratio_lightgbm.txt` | `107eba8d6a82050c447e575b83ea654b97e8d4a008a437da70a40d56673724e9` |
| `e9_energy_residual_lightgbm.txt` | `ff638336f2990014e8c7386caad075f7912337f555bb92f43875dfb6f40a9744` |
| `e9_inference_features.parquet` | `98ae18eab6e4468c9ba22bde3c679e04c14c2b0b752b29c12d54cfbc8485141e` |
| `feature_schema.json` | `57f7a0a8f05abffe040546c2ff6e5a74e76c67b910269b394460d56b412e74cf` |
| `reference_forecasts.parquet` | `b8dc3dfdffe08adf690d381acc8b149a9192af16bbf0a8c97a8b7a341e659dfe` |
| `statistical_models.joblib` | `30d02addb7a5fab377de6c1ef6a95ba5f971be6c9236d0632a4730a43cadcc5f` |
| `training_context.parquet` | `4538f5ae5e247a18bbcf96db9532513ff22c90502e302943f587e8158df3f859` |

## Validation

- Strict checksum and completeness validation passed in the production runner.
- Two fresh processes each generated 216 finite forecasts.
- Maximum absolute difference between fresh processes: 0.0.
- Both inference outputs had SHA-256 `5d3a1e1d96b30f853c7ebede856d35b0d5bbe19334ed4d93f2720eb1b550950a`.
- Four focused production tests passed, including corrupt-bundle rejection.
- Public output contains no P10/P90 or confidence fields.
- A simulated pre-publication failure preserved the prior valid output checksum.
- No live forecast was published and no scheduler was activated.

Historical backtest equality is intentionally not required: the frozen bundle
uses the approved 2026-10 production cutoff, while historical backtests used
their own forecast-origin cutoffs.
