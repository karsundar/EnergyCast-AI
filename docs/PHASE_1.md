# Phase 1: data acquisition, ingestion, and validation

## Deliverables

- Declarative dataset manifest with source URL, expected format, checksum when available, and coverage requirements.
- Idempotent downloader that reuses verified cached files.
- Deterministic ingestion into a documented canonical schema.
- Automated validation report covering file integrity, schema, timestamps, duplicates, missingness, ranges, and required coverage.

## Exit gate

Phase 1 is complete only when every configured dataset passes all required checks and the validation report records the exact source artifacts and checksums. Model training remains prohibited until then.

