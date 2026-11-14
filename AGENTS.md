# EnergyCast agent instructions

- Work incrementally, one phase at a time.
- Current phase: Phase 1 — data acquisition, automated ingestion, and validation.
- Do not begin model training until every configured dataset passes validation.
- Keep `docs/PROJECT_STATUS.md` current so another session can resume directly.
- Prefer deterministic Python scripts for data processing.
- Cache downloads and intermediate results; do not redownload unchanged data.
- Preserve existing working code and do not modify ROCm or ComfyUI environments.
- Train models locally when training is eventually authorized.
- Keep changes and responses concise; ask only when a decision materially changes the project.

