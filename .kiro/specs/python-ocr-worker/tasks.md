# Implementation Plan: Python OCR Worker

## Overview

This plan implements the asynchronous Python OCR Worker described in the design. Implementation is test-driven and incremental, building the **pure layers first** (config validation, plate normalization, OCR processing, message validation) because they carry most of the 12 correctness properties and require no I/O. The **I/O connectors** (`storage/`) are built next against `moto` and in-memory fakes. Finally the **Poller** wires everything together with the idempotency branch, ordered side effects, poison classification, and graceful shutdown.

All work lives in `worker/` as a Python 3.14 project managed by `uv`. The Worker MUST honor the exact shared contracts already used by the Go API: the `sessions` schema (`id`, `license_plate`, `status`, `s3_photo_key`), the Redis key `spots:available`, the `AuditoriaEstacionamento` item shape (`id = <session_id>#<timestamp_nano>`, `action`, `entity_id`, `timestamp` RFC3339Nano, `details`), and the SQS message body `{"session_id","s3_key"}` — no new names are introduced.

Property-based tests use `hypothesis` with a minimum of 100 examples each and are tagged **Feature: python-ocr-worker, Property {n}: {text}**, mapping to the 12 correctness properties in the design.

## Tasks

- [x] 1. Set up the `uv` project scaffold and package structure
  - Create `worker/pyproject.toml` for Python 3.14 managed by `uv` with runtime deps (`boto3`, `pytesseract`, `Pillow`, `psycopg`, `redis`) and dev deps (`pytest`, `hypothesis`, `moto`, `ruff`)
  - Configure `pytest` (test path `tests/`) and `ruff` (lint + format) in `pyproject.toml`
  - Create package directories with `__init__.py`: `worker/ocr/`, `worker/storage/`, and `tests/` mirroring the module layout
  - _Requirements: 14.1_

- [x] 2. Implement configuration loading and validation (pure)
  - [x] 2.1 Implement `Config` dataclass and `load_config` in `worker/config.py`
    - Define frozen `Config` dataclass with all fields from the design (endpoint, region, credentials, SQS/DB/Redis URLs, bucket, table)
    - Read all env vars listed in Req 14.1; treat `AWS_ENDPOINT_URL` as optional (absent/empty => default endpoints)
    - Raise `ConfigError` naming **every** missing/empty required var (collected, not first-fail) and preventing loop start with a non-zero startup path
    - Validate URL/scheme for `SQS_QUEUE_URL`, `DATABASE_URL`, `REDIS_URL`, and `AWS_ENDPOINT_URL` (http/https when present); abort when region is also empty and endpoint absent
    - Implement `redact()` returning a fixed marker for secret values
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 15.2, 15.3, 15.4_

  - [ ]* 2.2 Write property test for missing-variable reporting completeness
    - **Feature: python-ocr-worker, Property 10: Missing-variable reporting is complete**
    - Generate subsets of required env vars to remove/empty; assert the error names exactly that subset and blocks loop start
    - **Validates: Requirements 14.2**
    - _Requirements: 14.2_

  - [ ]* 2.3 Write property test for credential redaction
    - **Feature: python-ocr-worker, Property 11: Credentials never appear in log output**
    - Generate connection strings with embedded credentials; assert redaction marker present and the secret substring never appears
    - **Validates: Requirements 14.5**
    - _Requirements: 14.5_

  - [ ]* 2.4 Write example tests for optional endpoint and malformed URLs
    - Cover `AWS_ENDPOINT_URL` optional, malformed URL/scheme rejection, and endpoint-empty-and-region-empty abort
    - _Requirements: 14.3, 14.4, 15.2, 15.3, 15.4_

- [x] 3. Implement license plate normalization (pure)
  - [x] 3.1 Implement `Plate_Normalizer` in `worker/ocr/clean.py`
    - Define `PlateResult` dataclass and the Mercosul (`ABC1D23`) and Old_Format (`ABC1234`, hyphen stripped) regexes
    - `normalize(raw)`: strip non-alphanumeric, uppercase letters, cap at 7 chars, match a format; return canonical plate or an unreadable result with `plate=None` (never padded/partial)
    - Ensure idempotence on already-valid plates
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [ ]* 3.2 Write property test for normalization output invariant
    - **Feature: python-ocr-worker, Property 1: Normalization output invariant**
    - Arbitrary strings: output is uppercase-alnum only, length <= 7, and `normalize(normalize(x)) == normalize(x)`
    - **Validates: Requirements 5.1, 5.6**
    - _Requirements: 5.1, 5.6_

  - [ ]* 3.3 Write property test for valid-plate acceptance and idempotence
    - **Feature: python-ocr-worker, Property 2: Valid plates are accepted, canonicalized, and idempotent**
    - Generate valid Mercosul/Old_Format plates; assert `ok=True`, value unchanged, length within 1–16
    - **Validates: Requirements 5.2, 5.3, 5.6, 6.4**
    - _Requirements: 5.2, 5.3, 5.6, 6.4_

  - [ ]* 3.4 Write property test for unmatchable input rejection
    - **Feature: python-ocr-worker, Property 3: Unmatchable input yields an unreadable result with no padded value**
    - Generate inputs whose normalized form matches neither pattern (incl. empty/non-alnum); assert `ok=False`, `plate=None`
    - **Validates: Requirements 5.4, 5.5**
    - _Requirements: 5.4, 5.5_

- [x] 4. Implement OCR processing (pure over image bytes)
  - [x] 4.1 Implement `OCR_Processor` in `worker/ocr/processor.py`
    - Define `OcrResult` dataclass and `extract_text(image_bytes, timeout_s=10.0)`
    - Apply rescale -> grayscale -> binary threshold (in that order) then Tesseract; return raw text on success
    - Return typed errors (never raise for domain outcomes): `decode` on undecodable bytes (skip Tesseract), `no_text` on empty OCR output, `timeout` when >10s
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ]* 4.2 Write example tests for OCR_Processor
    - Assert transform order, fixture image -> known text, undecodable bytes skip Tesseract, empty-output error, timeout abort
    - _Requirements: 4.1, 4.2, 4.4, 4.5, 4.6_

- [x] 5. Implement message parsing and validation (pure)
  - [x] 5.1 Implement `Session_Message` parse/validate in `worker/worker.py`
    - Parse the raw SQS body as JSON without unwrapping any SNS envelope; extract `session_id` and `s3_key` as UTF-8 strings
    - Validate: `session_id` exactly 32 hex chars, `s3_key` non-empty and <= 1024 chars; classify every other body as poison/invalid with a specific reason, leaving the original body unmodified
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [ ]* 5.2 Write property test for message validation classification
    - **Feature: python-ocr-worker, Property 4: Message validation classifies exactly the well-formed payloads**
    - Generate valid payloads, malformed JSON, missing/empty/oversized fields, non-hex/wrong-length `session_id`; assert accept-iff-well-formed with reason and body unchanged
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6**
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

- [x] 6. Checkpoint - Ensure all pure-layer tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Implement the AWS client factory
  - [x] 7.1 Implement `worker/storage/clients.py`
    - `build_boto3_session(cfg)` plus `s3_client`/`sqs_client`/`dynamodb_client` applying identical endpoint resolution (`cfg.aws_endpoint_url or None`) to every client
    - _Requirements: 15.1, 15.5_

  - [ ]* 7.2 Write property test for uniform endpoint resolution
    - **Feature: python-ocr-worker, Property 12: Uniform endpoint resolution across AWS clients**
    - Generate valid configs (with/without endpoint); assert S3/SQS/DynamoDB clients resolve to the same endpoint
    - **Validates: Requirements 15.5**
    - _Requirements: 15.1, 15.5_

- [x] 8. Implement the S3 connector
  - [x] 8.1 Implement `S3_Connector` in `worker/storage/s3_store.py`
    - `download(key)` within 10s, cap at 10MB via `ContentLength` check + bounded read, 3 retries on transient errors
    - Raise `RetrievalError('not_found'|'too_large'|'invalid_params'|'unavailable')`; guard empty/absent params before download; discard buffer on overflow; release content on success or failure
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

  - [ ]* 8.2 Write example tests for S3_Connector using moto
    - Cover invalid-params guard, near-limit buffer, oversize abort + release, 3-retry exhaustion, buffer release on success/failure, not-found
    - _Requirements: 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

- [x] 9. Implement the Session repository (RDS)
  - [x] 9.1 Implement `Session_Repository` in `worker/storage/session_repo.py`
    - `SessionRow` mapping the exact `sessions` schema; read `DATABASE_URL`; `get(session_id)` returns row or None
    - `mark_parked(session_id, plate)`: conditional `UPDATE sessions SET license_plate=%s, status='PARKED' WHERE id=%s AND status='PROCESSING'`; return True iff exactly one row updated (the idempotency gate); leave record unchanged on connection/query error
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 11.5_

  - [ ]* 9.2 Write example tests for Session_Repository with an in-memory fake
    - Cover not-found path, injected DB error leaves record unchanged, non-PROCESSING status yields no update, `id`/`status` mapping to the exact schema
    - _Requirements: 6.3, 6.5, 6.6_

- [x] 10. Implement the Spots counter (Redis)
  - [x] 10.1 Implement `Spots_Counter` in `worker/storage/spots.py`
    - `decrement()`: atomic `DECR spots:available` within 500ms; on result `< 0`, `SET spots:available 0` and emit underflow error; retry up to 3x on connection error with message unacknowledged
    - Read Redis target from `REDIS_URL`; fail startup if absent/empty
    - _Requirements: 7.1, 7.2, 7.3, 7.5, 7.6_

  - [ ]* 10.2 Write example tests for Spots_Counter with an in-memory fake
    - Cover underflow clamp at 0 + error, 3-retry on connection error, exact key `spots:available`
    - _Requirements: 7.5, 7.6_

- [x] 11. Implement the Audit logger (DynamoDB)
  - [x] 11.1 Implement `Audit_Logger` in `worker/storage/audit.py`
    - `log_ocr(session_id, plate)`: PutItem with `action=OCR_PROCESSING`, `id=f'{session_id}#{time.time_ns()}'`, `entity_id=session_id`, `timestamp` RFC3339Nano, `details={'license_plate','session_id'}`; conditional put on `attribute_not_exists(id)`; 3 retries
    - `log_poison(session_id, reason)` for poison classification records; read `DYNAMODB_TABLE_NAME`, fail init if missing
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 12.1_

  - [ ]* 11.2 Write property test for audit idempotency per transition
    - **Feature: python-ocr-worker, Property 7: Audit write is idempotent per transition**
    - Using moto/fake, assert at most one `OCR_PROCESSING` entry per transition because the conditional put rejects duplicate `id`
    - **Validates: Requirements 8.1, 8.4**
    - _Requirements: 8.1, 8.4_

  - [ ]* 11.3 Write example tests for Audit_Logger item shape and retries
    - Assert `id` format `<session_id>#<timestamp_nano>`, details contain plate + session_id, retry-then-error path
    - _Requirements: 8.2, 8.3, 8.5_

- [x] 12. Checkpoint - Ensure all connector tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 13. Implement the Poller orchestration and outcome mapping
  - [x] 13.1 Implement `Poller` core in `worker/worker.py`
    - Constructor injecting cfg + all connectors + ocr + normalizer; `Outcome` enum (`DELETE`/`RETAIN`/`POISON`)
    - `_handle(msg)`: parse/validate -> session lookup -> idempotency branch (PARKED/PAID/not-found => DELETE; lookup failure => RETAIN) -> S3 download -> OCR -> normalize -> ordered side effects (RDS conditional UPDATE -> Redis DECR only when `mark_parked()==True` -> DynamoDB PutItem -> SQS DeleteMessage)
    - Map every error per the design taxonomy: fail safe toward RETAIN; unreadable plate leaves status PROCESSING and does not delete
    - _Requirements: 1.3, 1.4, 6.6, 7.1, 7.4, 9.1, 9.2, 10.1, 10.2, 10.3, 11.1, 11.2, 11.3, 11.4, 11.5, 13.1, 13.3_

  - [ ]* 13.2 Write property test for exactly-once transition and decrement
    - **Feature: python-ocr-worker, Property 5: Exactly-once transition and spots decrement per session**
    - Random redelivery multiplicities and starting statuses via in-memory fakes; assert one transition + one DECR when starting PROCESSING, none otherwise
    - **Validates: Requirements 6.6, 7.1, 7.4, 11.5**
    - _Requirements: 6.6, 7.1, 7.4, 11.5_

  - [ ]* 13.3 Write property test for delete-vs-retain outcome
    - **Feature: python-ocr-worker, Property 6: Delete-vs-retain outcome is determined solely by processing result**
    - Generate session states + injected step failures; assert delete-iff-terminal-success and retain-on-any-error/unreadable/lookup-failure
    - **Validates: Requirements 1.4, 9.1, 9.2, 10.1, 10.3, 11.1, 11.2, 11.3, 11.4, 13.1, 13.2**
    - _Requirements: 1.4, 9.1, 9.2, 10.1, 10.3, 11.1, 11.2, 11.3, 11.4, 13.1, 13.2_

  - [ ]* 13.4 Write property test for ordered side effects never partially committing
    - **Feature: python-ocr-worker, Property 9: Ordered side effects never partially commit**
    - Inject failures at each step; assert no later side effect runs and DECR only after RDS transition commits
    - **Validates: Requirements 13.3**
    - _Requirements: 13.3_

- [x] 14. Implement poison classification and receive-count handling
  - [x] 14.1 Implement poison classification in `worker/worker.py`
    - `_receive_count(msg)` reads SQS system attribute `ApproximateReceiveCount`; classify as `POISON` when it exceeds the configured max receive count (3)
    - On poison: record reason via `Audit_Logger.log_poison` (retry up to 3x, then continue), rely on SQS redrive to move the message; continue the loop within 1s without terminating
    - _Requirements: 10.4, 12.1, 12.2, 12.3, 12.4, 12.5_

  - [ ]* 14.2 Write property test for poison classification boundary
    - **Feature: python-ocr-worker, Property 8: Poison classification is driven by receive count**
    - Generate receive counts around the threshold; assert poison iff count exceeds max, not on count alone below/at threshold
    - **Validates: Requirements 10.4, 12.2**
    - _Requirements: 10.4, 12.2_

- [x] 15. Implement the polling loop, resilience, and graceful shutdown
  - [x] 15.1 Implement `run()`, `_receive()`, and signal handling in `worker/worker.py`
    - `_receive()` long-polls with `WaitTimeSeconds=20`, max 10 messages; empty receive re-polls within 1s; process each message sequentially
    - On receive connection/authorization error, back off <=30s and continue without terminating; per-message exceptions are caught, logged with failed step + dependency, loop continues within 1s
    - `request_stop` handles SIGTERM/SIGINT: stop new receives within 1s, finish in-flight within a 30s bound (abandon without delete if exceeded), close RDS/Redis/AWS connections continuing past individual close failures, return exit code 0 iff all in-flight completed else non-zero
    - _Requirements: 1.1, 1.2, 1.5, 1.6, 1.7, 9.4, 12.4, 13.4, 16.1, 16.2, 16.3, 16.4, 16.5_

  - [ ]* 15.2 Write example tests for loop resilience and shutdown
    - Cover empty-receive re-poll, receive-error retry-and-continue, signal stops new receives, in-flight completion within 30s, abandon-on-timeout, connection-close resilience and exit code
    - _Requirements: 1.2, 1.5, 12.4, 13.4, 16.1, 16.2, 16.3, 16.4, 16.5_

- [x] 16. Wire the entrypoint together
  - [x] 16.1 Implement `worker/worker.py` `__main__` startup
    - Load config (fail fast, non-zero exit on `ConfigError`), build clients via the factory, construct connectors + OCR + normalizer, install signal handlers, and run the Poller returning its exit code
    - Ensure no orphaned modules: every component from tasks 2–15 is constructed and injected here
    - _Requirements: 1.7, 6.2, 7.3, 8.6, 14.2, 16.4_

  - [ ]* 16.2 Write smoke test for startup wiring
    - Assert all env vars read (Req 14.1), queue configured with 300s visibility expectation surfaced (Req 9.4), and startup aborts on missing required config
    - _Requirements: 14.1, 9.4_

- [x] 17. Update tooling and entrypoint integration
  - [x] 17.1 Update the `dev:worker` Taskfile command for uv
    - Change `worker/` `dev:worker` command from `python3 worker.py` to `uv run python worker.py`, keeping the existing local env vars; add missing `REDIS_URL`, `S3_BUCKET_NAME`, and `DYNAMODB_TABLE_NAME` env entries to match the loaded config
    - _Requirements: 14.1, 1.6, 6.2, 7.2, 8.6_

- [x] 18. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass with `uv run pytest`, run `uv run ruff check .`, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP, but each maps a design property or example scenario to the requirements it validates.
- **Property coverage:** Properties 1–3 (task 3), 4 (task 5), 5/6/9 (task 13), 7 (task 11), 8 (task 14), 10/11 (task 2), 12 (task 7). All 12 correctness properties are covered.
- Each task references specific requirements for traceability; the pure layers are built and tested before any I/O connector.
- **Infra dependency (not a worker/ code task):** The Worker depends on an SQS **DLQ + redrive policy** with `maxReceiveCount = 3`, which `infra/main.tf` does not currently define (it has only `ocr_queue` with `message_retention_seconds`). Requirements 9.3, 12.2, 12.3, and 13.5 cannot be satisfied at runtime until the infra module adds a second `aws_sqs_queue` (the DLQ) and a `redrive_policy` on `ocr_queue`. This lives in the `infra/` module, outside `worker/`, so it is tracked here as a blocking dependency rather than a worker coding task. The DLQ/redrive integration test is likewise blocked until then.
- **Requirement discrepancies flagged in design (confirm with owner):** `maxReceiveCount` 3 (Req 12.2/12.3) vs 5 delivery attempts (Req 9.3/13.5) — design treats 3 as the SQS redrive threshold and 5 as an application-side upper bound. Visibility timeout 300s (Req 9.4) vs 30s (Req 13.2) — design uses 300s configured, treating 30s as the minimum redelivery latency expectation.
- Shared contracts are fixed: `sessions` schema, Redis key `spots:available`, `AuditoriaEstacionamento` item shape, and the `{"session_id","s3_key"}` SQS body — no new names are introduced.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "4.1", "5.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "3.2", "3.3", "3.4", "4.2", "5.2", "7.1", "8.1", "9.1", "10.1", "11.1"] },
    { "id": 3, "tasks": ["7.2", "8.2", "9.2", "10.2", "11.2", "11.3", "13.1"] },
    { "id": 4, "tasks": ["13.2", "13.3", "13.4", "14.1"] },
    { "id": 5, "tasks": ["14.2", "15.1"] },
    { "id": 6, "tasks": ["15.2", "16.1", "17.1"] },
    { "id": 7, "tasks": ["16.2"] }
  ]
}
```
