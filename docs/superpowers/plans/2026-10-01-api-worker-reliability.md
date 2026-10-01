# API and Worker Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement reliability improvements across the Go API and Python OCR Worker: handle SQS publication failures, add state guards on exit payment, mark sessions as FAILED on poison/DLQ delivery, and refactor `worker.py` into focused modules under 500 lines.

**Architecture:**
- **Go API (`api/internal/service`)**: `CreateEntry` ensures SQS publish success or fails fast (and rolls back or marks entry); `PayExit` verifies the session is in `PARKED` status before attempting payment. Fix repository test signature for `GetAvailable`.
- **Worker (`worker/storage`, `worker/worker.py`)**: When an SQS message reaches max delivery attempts (`receive_count >= MAX_RECEIVE_COUNT`) or is classified as poison, mark the session status in PostgreSQL as `FAILED` (via conditional update `WHERE status='PROCESSING'`) and audit the event in DynamoDB.
- **Worker Refactoring**: Extract SQS message parsing, validation, and types into `worker/parser.py`, keeping `worker/worker.py` as a concise poller & lifecycle manager under 400 lines.

**Tech Stack:** Go 1.27, Python 3.14 (uv, pytest, boto3, psycopg-binary, redis), PostgreSQL, DynamoDB, SQS.

---

### Task 1: Fix Go Repository Test Signature & Add State Checks in Go API

**Files:**
- Modify: `api/internal/repository/repository_test.go:157-165`
- Modify: `api/internal/service/parking.go`
- Modify: `api/internal/service/parking_test.go`

**Interfaces:**
- Consumes: `repository.SessionRepository`, `repository.EventPublisher`, `repository.SpotsRepository`
- Produces: `ErrInvalidSessionStatus`, `ErrPublishEventFailed` in `api/internal/service/parking.go`

- [ ] **Step 1: Fix `repository_test.go` signature**
In `api/internal/repository/repository_test.go`, update line 158:
Change `spots, err := repo.GetAvailable(ctx, 50)` to `spots, err := repo.GetAvailable(ctx)`. Run `go test ./...` in `api/` to verify it compiles and unit tests pass.

- [ ] **Step 2: Write failing unit test for `CreateEntry` SQS failure & `PayExit` state guard**
In `api/internal/service/parking_test.go`, add:
- `TestCreateEntry_PublishFailure`: when `publisher.Publish` returns an error, `CreateEntry` returns an error and does not leave an active ghost session without an error.
- `TestPayExit_InvalidStatus`: when session status is not `PARKED` (e.g. `PROCESSING` or `PAID`), `PayExit` returns `service.ErrInvalidSessionStatus`.

- [ ] **Step 3: Run tests to verify they fail**
Run: `go test -v ./internal/service -run "TestCreateEntry_PublishFailure|TestPayExit_InvalidStatus"`
Expected: FAIL

- [ ] **Step 4: Implement minimal code in `api/internal/service/parking.go`**
- Define `var ErrInvalidSessionStatus = errors.New("session is not in PARKED status")`.
- In `CreateEntry`:
  ```go
  if err := s.publisher.Publish(ctx, map[string]string{
      "session_id": sessionID,
      "s3_key":     s3Key,
  }); err != nil {
      return nil, fmt.Errorf("failed to publish entry event: %w", err)
  }
  ```
- In `PayExit`:
  ```go
  if session.Status != "PARKED" {
      return nil, ErrInvalidSessionStatus
  }
  ```

- [ ] **Step 5: Run tests to verify they pass**
Run: `go test -v ./internal/service/...`
Expected: PASS

- [ ] **Step 6: Commit changes**
Run:
```bash
git add api/internal/repository/repository_test.go api/internal/service/parking.go api/internal/service/parking_test.go
git commit -m "fix(api): handle sqs publish failure and validate parked status on payment"
```

---

### Task 2: Worker - Mark RDS Session as `FAILED` on Poison Message / DLQ

**Files:**
- Modify: `worker/storage/session_repo.py`
- Modify: `worker/worker.py`
- Modify: `worker/tests/fakes.py`
- Modify: `worker/tests/test_poison.py`

**Interfaces:**
- Produces: `SessionRepository.mark_failed(session_id: str, reason: str) -> bool`
- Updates: `Poller._handle` to call `mark_failed` when classified as `POISON` if `session_id` is known.

- [ ] **Step 1: Write failing test in `worker/tests/test_poison.py`**
Add test `test_poison_message_marks_session_failed_in_rds`:
Verify that when a message is received with `receive_count >= MAX_RECEIVE_COUNT` and processing fails (e.g. unreadable plate or bad photo), the session repository records status `FAILED`.

- [ ] **Step 2: Run test to verify it fails**
Run: `uv run --directory worker pytest tests/test_poison.py`
Expected: FAIL

- [ ] **Step 3: Implement `mark_failed` in `SessionRepository` and update `worker.py`**
In `worker/storage/session_repo.py`:
Add method:
```python
def mark_failed(self, session_id: str, reason: str | None = None) -> bool:
    """Mark a session as FAILED if currently PROCESSING (terminal state on DLQ)."""
    with self._conn.cursor() as cur:
        cur.execute(
            "UPDATE sessions SET status = 'FAILED' WHERE id = %s AND status = 'PROCESSING'",
            (session_id,),
        )
        return cur.rowcount > 0
```
Update `worker/tests/fakes.py` `FakeSessionRepo` to implement `mark_failed`.
In `worker/worker.py`, when a message is classified as `Outcome.POISON`, call `self._sessions.mark_failed(session_id, reason)`.

- [ ] **Step 4: Run tests to verify they pass**
Run: `uv run --directory worker pytest`
Expected: 88+ passed

- [ ] **Step 5: Commit changes**
Run:
```bash
git add worker/storage/session_repo.py worker/worker.py worker/tests/fakes.py worker/tests/test_poison.py
git commit -m "feat(worker): mark session as FAILED in rds when message enters dlq"
```

---

### Task 3: Worker - Refactor `worker.py` to modular submodules (< 500 lines)

**Files:**
- Create: `worker/parser.py`
- Modify: `worker/worker.py`
- Modify: `worker/tests/test_clean.py`, `worker/tests/test_poison.py`, `worker/tests/test_poller_side_effects.py` (if importing message parsing)

**Interfaces:**
- `worker/parser.py`: exports `SessionMessage`, `ValidationResult`, `parse_session_message`, `MAX_BODY_BYTES`, `SESSION_ID_RE`, `MAX_S3_KEY_LEN`.
- `worker/worker.py`: imports `parse_session_message`, `SessionMessage` from `parser.py`, keeping Poller and lifecycle logic clean, focused, and under 500 lines.

- [ ] **Step 1: Extract `worker/parser.py`**
Move `SessionMessage`, `ValidationResult`, `parse_session_message`, and regex constants from `worker/worker.py` into `worker/parser.py`.
Re-export them in `worker/worker.py` or import them directly.

- [ ] **Step 2: Run all tests to ensure zero regressions**
Run: `uv run --directory worker pytest`
Run: `uv run --directory worker ruff check .`
Expected: PASS

- [ ] **Step 3: Verify line counts**
Check lines of `worker/worker.py` and `worker/parser.py`: ensure both are well within limits (< 500 lines).

- [ ] **Step 4: Commit changes**
Run:
```bash
git add worker/parser.py worker/worker.py
git commit -m "refactor(worker): extract message parsing into worker/parser.py"
```

---

### Task 4: Integration Verification & Pull Request Creation

- [ ] **Step 1: Run all test suites across the repository**
Run:
- Go tests: `go test -v ./...` in `api/`
- Python tests: `uv run --directory worker pytest`
- Ruff linter: `uv run --directory worker ruff check .`

- [ ] **Step 2: Push branch and create Pull Request**
Create PR via `rtk gh pr create` detailing the reliability improvements and refactoring done.
