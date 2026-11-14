# Project status

Last updated: 2026-11-14

## Current state

- Git repository initialized on branch `main`.
- Phase 1 is active.
- No dataset has been selected or downloaded.
- No training work has started.
- Existing ROCm and ComfyUI environments have not been modified.

## Blocking decision

Before implementing acquisition, specify the authoritative dataset source(s), target geography or grid, forecast target, sampling interval, and required historical date range. These choices determine schemas and validation rules and must not be guessed.

## Next action

1. Record the dataset decision in `config/datasets.example.json` or replace it with `config/datasets.json`.
2. Implement a standard-library downloader with checksums and cache reuse.
3. Implement source-specific ingestion and validation.
4. Produce a machine-readable validation report.
5. Do not proceed to training unless the Phase 1 gate passes.

