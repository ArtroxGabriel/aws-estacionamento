# Design Document: API on RabbitMQ and the Exit Barrier

## Overview

Two changes land together because they touch the same files and are verified by the same end-to-end flow:

- **Infrastructure swap (no business rule changes).** `repository/sqs.go` is replaced by `repository/rabbitmq.go` behind the existing `EventPublisher` interface. The single `AWS_ENDPOINT_URL` becomes `S3_ENDPOINT_URL` and `DYNAMODB_ENDPOINT_URL`. The service and the handlers do not notice.
- **Exit barrier (business rule change).** The status model gains `EXITED`. The spot moves from "released at the payment" to "released at the exit barrier".

Source documents: `docs/PLANO-TP2.md` (Components 1, 2, 4 and section 5), `docs/PLANO-TP2-WORKER.md` (queue contract) and `docs/DECISOES.md` D20 (the ticket identifies the car).

## Decisions

Section 5.2 of `docs/PLANO-TP2.md` lists open decisions. This spec settles them as follows. Each one is a default that the author can overturn before implementation.

| # | Decision | Reason |
|---|---|---|
| D-A1 | Keep the two barriers: `PAID` holds the spot, `EXITED` frees it | Requested for this feature; it is extra scope relative to the rubric |
| D-A2 | The payment route stays `POST /exits/{id}/pay` and its audit action stays `EXIT_PAYMENT` | Renaming to `/sessions/{id}/pay` and `PAYMENT_RECEIVED` would break the Web, the tests and the documents for no functional gain |
| D-A3 | The exit by plate takes a typed/recognized plate as JSON. No photo, no OCR in the request | The architecture forbids synchronous OCR, and D20 forbids depending on the plate. The camera of the exit totem is simulated by the plate field |
| D-A4 | A plate matches only when it is a Known plate (`READ` or `MANUAL`) of an Occupying session; among several `PAID` matches the oldest `paid_at` wins | Deterministic, and a repeated plate never opens the barrier for an unpaid session |
| D-A5 | New column `paid_at`; `exited_at` now means "crossed the exit barrier" | Today `MarkAsPaid` writes `exited_at`, which would be false under the new rule |
| D-A6 | Existing `PAID` rows become `EXITED` in the migration | Under the old rule they already left and already freed their spot; the counter stays valid |
| D-A7 | Variables are `S3_ENDPOINT_URL`, `DYNAMODB_ENDPOINT_URL`, `RABBITMQ_URL`, `RABBITMQ_QUEUE_NAME`, with no compose-host defaults (empty endpoint = AWS) | Same names as the Worker and `Taskfile.yaml`; the plan's `RustFSEndpoint`/`DynamoDBEndpoint` defaults would make "empty = AWS" impossible |
| D-A8 | The API only checks the queue (passive declaration) | The topology belongs to `infra/rabbitmq/definitions.json`; a declaration with other arguments fails with `PRECONDITION_FAILED` |
| D-A9 | The publisher connects lazily and never blocks startup | "A queue outage does not take the entry down" (root `AGENTS.md`) |
| D-A10 | Plate correction is allowed while `PARKED` or `PAID` | A paid car can still gain a plate before the barrier; harmless because the ticket works anyway |
| D-A11 | Frontend routes follow the plan (`/cancela-entrada`, `/pagamento`, `/cancela-saida`); the old paths redirect | The plan names them; redirects keep old links alive |
| D-A12 | No Redis counter rebuild at startup | The lazy rebuild in `GetAvailableSpots`/`reserveSpot` already covers a missing key |

Not decided here and left as they are: the Kubernetes bonus and the Trabalho 1 AWS deployment (`infra/*.tf` still exports `SQS_QUEUE_URL`). After this feature the API image no longer works with that deployment, like the Worker image.

## Architecture

```mermaid
flowchart LR
    subgraph Web["web (nginx :3000)"]
        E["/cancela-entrada"]
        P["/pagamento"]
        S["/cancela-saida"]
    end
    E -->|POST /entries| API
    P -->|POST /exits/:id/pay| API
    S -->|POST /barrier/exit/plate or /ticket| API
    API["api (:8080)"] -->|reserve / release| Redis[(redis)]
    API -->|sessions| PG[(postgres)]
    API -->|photo| RustFS[(rustfs)]
    API -->|publish, confirmed| Q{{"rabbitmq: ocr-processamento-fila"}}
    API -->|audit| DDB[(dynamodb-local)]
    Q --> Worker["worker"]
    Worker -->|plate_status| PG
    Worker -->|audit| DDB
```

### Session state machine

```mermaid
stateDiagram-v2
    [*] --> PARKED: POST /entries (spot reserved)
    PARKED --> PAID: POST /exits/{id}/pay (spot kept)
    PAID --> EXITED: POST /barrier/exit/* (spot released)
    PARKED --> [*]: DELETE (spot released)
    PAID --> [*]: DELETE (spot released)
    EXITED --> [*]: DELETE (no counter change)
```

`plate_status` (`PENDING` -> `READ` | `UNREADABLE`, or `MANUAL`) is orthogonal and never guards a transition.

## Components and Interfaces

### `internal/config/config.go`

Remove `AWSEndpointURL` and `SQSQueueURL`. Add:

| Field | Variable | Default |
|---|---|---|
| `S3EndpointURL` | `S3_ENDPOINT_URL` | empty (AWS) |
| `DynamoDBEndpointURL` | `DYNAMODB_ENDPOINT_URL` | empty (AWS) |
| `RabbitMQURL` | `RABBITMQ_URL` | empty (publisher reports failure) |
| `RabbitMQQueueName` | `RABBITMQ_QUEUE_NAME` | `ocr-processamento-fila` |

### `internal/repository/rabbitmq.go` (new, replaces `sqs.go`)

```go
type RabbitMQEventPublisher struct {
    url   string
    queue string
    mu    sync.Mutex        // one publication at a time on the channel
    conn  *amqp091.Connection
    ch    *amqp091.Channel
    dial  func(url string) (*amqp091.Connection, error) // injectable for tests
}

func NewRabbitMQEventPublisher(cfg config.Config) *RabbitMQEventPublisher
func (p *RabbitMQEventPublisher) Publish(ctx context.Context, payload any) error
func (p *RabbitMQEventPublisher) Close() error
```

`Publish`:

1. Empty URL: return `ErrPublisherNotConfigured`.
2. Derive a context with a 5 s timeout.
3. Under the mutex, `ensureChannel`: if there is no open connection/channel, dial, open a channel, `Confirm(false)`, `QueueDeclarePassive(queue)`. Any error closes what was opened and is returned.
4. `PublishWithDeferredConfirmWithContext` to exchange `""`, routing key `queue`, `mandatory=true`, `DeliveryMode: Persistent`, `ContentType: "application/json"`.
5. Wait for the confirmation within the context. A nack, a returned message or a timeout is an error. On any error drop the connection so the next call reconnects.

The connection is created on the first publication, not in the constructor (D-A9). `Close` is called from the `fx` lifecycle `OnStop`. The URL is logged only through a redaction helper (userinfo removed).

A channel that fails a passive declaration is closed by the broker, which is why step 3 discards the channel on error.

### `internal/repository/s3.go`

`EnsureBucket(ctx)`: `HeadBucket`, and on "not found" `CreateBucket`, treating `BucketAlreadyOwnedByYou`/`BucketAlreadyExists` as success. Called from the lifecycle `OnStart` only when `S3EndpointURL` is set. A failure is logged and does not stop the API: `createbuckets` in the compose already creates the bucket, this is a second safety net.

### `cmd/api/main.go`

- `NewS3Client`: path-style and `BaseEndpoint` when `S3EndpointURL` is set.
- `NewDynamoDBClient`: `BaseEndpoint` when `DynamoDBEndpointURL` is set.
- Remove `NewSQSClient`; provide `NewRabbitMQEventPublisher` as `repository.EventPublisher`.
- Lifecycle: `EnsureBucket` on start, publisher `Close` on stop.

### `internal/model/session.go`

Add `StatusExited = "EXITED"` and `PaidAt *time.Time` (`json:"paid_at,omitempty"`).

### `internal/repository/postgres.go` and `interfaces.go`

| Method | SQL |
|---|---|
| `MarkAsPaid(ctx, id, paidAt, amount)` | `UPDATE sessions SET status='PAID', paid_at=$2, amount_paid=$3 WHERE id=$1 AND status='PARKED' RETURNING ...` |
| `MarkAsExited(ctx, id, exitedAt)` (new) | `UPDATE sessions SET status='EXITED', exited_at=$2 WHERE id=$1 AND status='PAID' RETURNING ...` |
| `FindOccupyingByPlate(ctx, plate)` (new) | `SELECT ... WHERE license_plate=$1 AND plate_status IN ('READ','MANUAL') AND status IN ('PARKED','PAID') ORDER BY paid_at ASC NULLS LAST, entered_at ASC` |
| `UpdatePlate` | condition becomes `status IN ('PARKED','PAID')` |
| `CountActive` | `WHERE status IN ('PARKED','PAID')` |

`sessionColumns` gains `paid_at`. The plan calls the plate lookup `FindByPlateActive`; it returns a list here because a plate can repeat (D-A4).

### Migration `000003_exit_barrier`

```sql
-- up
ALTER TABLE sessions ADD COLUMN paid_at TIMESTAMP WITH TIME ZONE NULL;
UPDATE sessions SET paid_at = exited_at, status = 'EXITED' WHERE status = 'PAID';
CREATE INDEX IF NOT EXISTS idx_sessions_plate_occupying
    ON sessions (license_plate) WHERE status IN ('PARKED', 'PAID');

-- down
DROP INDEX IF EXISTS idx_sessions_plate_occupying;
UPDATE sessions SET status = 'PAID', exited_at = COALESCE(exited_at, paid_at) WHERE status IN ('PAID', 'EXITED');
ALTER TABLE sessions DROP COLUMN paid_at;
```

The `down` script loses the difference between "paid and inside" and "left"; after a rollback the Redis key must be deleted so it is rebuilt.

### `internal/service/parking.go`

New errors: `ErrPaymentPending`, `ErrAlreadyExited`, `ErrPlateNotFound`, `ErrInvalidTicket`.

- `PayExit`: unchanged checks; calls `MarkAsPaid` with the payment time; **removes** `spotsRepo.Increment`. Audit `EXIT_PAYMENT` unchanged.
- `ExitBarrierByTicket(ctx, ticketID)`: `GetByID` -> nil: `ErrSessionNotFound`; `PARKED`: `ErrPaymentPending`; `EXITED`: `ErrAlreadyExited`; `PAID`: `openBarrier(session, "ticket")`.
- `ExitBarrierByPlate(ctx, rawPlate)`: `normalizePlate`; `FindOccupyingByPlate`; none: `ErrPlateNotFound`; first `PAID` in the returned order: `openBarrier(session, "plate")`; otherwise `ErrPaymentPending`.
- `openBarrier(ctx, session, method)`: `MarkAsExited`; nil result (lost a race): `ErrAlreadyExited`; otherwise `spotsRepo.Increment` and audit `EXIT_BARRIER_OPENED` with `status`, `method`, `license_plate`.
- `UpdatePlate`: accepts `PARKED` and `PAID`.
- `DeleteSession`: increments when the deleted session was `PARKED` or `PAID`.

`Increment` keeps its current semantics (no write when the key is missing; the next read rebuilds from PostgreSQL).

### `internal/handler/handler.go`

| Route | Success | Errors |
|---|---|---|
| `POST /barrier/exit/ticket` `{"ticket_id"}` | 200 session | 400 invalid body, 404 `session_not_found`, 409 `payment_pending`, 409 `already_exited` |
| `POST /barrier/exit/plate` `{"plate"}` | 200 session | 400 invalid body or plate, 404 `plate_not_found`, 409 `payment_pending`, 409 `already_exited` (lost race) |

Error bodies of these two routes carry a machine-readable code next to the message, `{"error": "...", "code": "payment_pending"}`, so the Web does not parse text. Existing routes keep their current bodies. `GET /sessions?status=EXITED` works through the existing filter.

### Web (`web/`)

- `types/api.ts`: `SessionStatus` gains `"EXITED"`; `Session.paid_at`; audit action `EXIT_BARRIER_OPENED`.
- `services/api.ts`: `exitByPlate(plate)`, `exitByTicket(ticketId)`; `ApiError` exposes `code`.
- `pages/ExitBarrierPage.tsx` (new): plate form, "use the paper ticket" fallback, barrier state (closed / open / denied) for the four outcomes.
- `App.tsx`: routes of D-A11 and redirects; `Layout` navigation updated.
- `PaymentPage`: success message points to the exit barrier.
- `DashboardPage`: lists `PARKED` and `PAID` (two requests or `status=ALL` filtered client-side), with a "paid" badge.
- `AuditPage`: label for `EXIT_BARRIER_OPENED`.

### Compose and Taskfile

```yaml
api:
  build: ../api
  image: estacionamento-api
  ports: ['8080:8080']
  environment:
    AWS_REGION: us-east-1
    AWS_ACCESS_KEY_ID: minioadmin
    AWS_SECRET_ACCESS_KEY: minioadmin
    S3_ENDPOINT_URL: http://rustfs:9000
    S3_BUCKET_NAME: estacionamento-fotos-veiculos-local
    DYNAMODB_ENDPOINT_URL: http://dynamodb-local:8000
    DYNAMODB_TABLE_NAME: AuditoriaEstacionamento
    RABBITMQ_URL: amqp://guest:guest@rabbitmq:5672/
    RABBITMQ_QUEUE_NAME: ocr-processamento-fila
    DATABASE_URL: postgres://app_user:app_password@postgres:5432/estacionamento?sslmode=disable
    REDIS_URL: redis:6379
  depends_on: # healthy: postgres, redis, rabbitmq, rustfs, dynamodb-local; completed: createbuckets, createtables
web:
  build: ../web
  image: estacionamento-web
  ports: ['3000:80']
  environment:
    API_UPSTREAM: http://api:8080
  depends_on: [api]
```

Values must be checked against the existing services of `infra/docker-compose.yaml` during implementation (table name, credentials, the port nginx listens on). The API image is `FROM scratch`, so it has no shell for a container healthcheck; `web` depends on `api` with `service_started`, and nginx answers 502 for the few seconds before the API listens.

The API applies its own migrations at startup (`repository/migrate.go`), so the Worker's `depends_on` does not change, but the Worker only finds the `sessions` table once the API ran. With the full compose this is automatic.

`Taskfile.yaml`: `up`, `down`, `logs` (compose in `infra/`), `test` (`test:api`, the Worker's `uv run pytest`, `test:web`).

## Data Models

`sessions` after migration `000003`:

| Column | Meaning |
|---|---|
| `status` | `PARKED`, `PAID`, `EXITED` |
| `plate_status` | `PENDING`, `READ`, `UNREADABLE`, `MANUAL` |
| `entered_at` | entry barrier |
| `paid_at` | payment (new) |
| `exited_at` | exit barrier |
| `amount_paid` | set at the payment |

Audit actions written by the API: `ENTRY`, `EXIT_PAYMENT`, `EXIT_BARRIER_OPENED` (new), `PLATE_CORRECTION`, `SESSION_DELETE`. The Worker's actions are unchanged.

Queue message: unchanged, `{"session_id": "<32 hex>", "s3_key": "photos/..."}`. The Worker rejects any other `session_id` shape.

## Correctness Properties

1. **Spot conservation.** For any sequence of entries, payments, exits and deletions applied to a lot of `N` spots with a working counter, `spots:available = N - |PARKED| - |PAID|` after every operation.
2. **Single release.** For any session and any interleaving of exit requests (plate and ticket, repeated), the counter is incremented at most once and exactly one request receives HTTP 200.
3. **No exit without payment.** No sequence of requests moves a session to `EXITED` unless it was `PAID` first.
4. **Ticket always works.** For every `PAID` session and every plate status, `POST /barrier/exit/ticket` opens the barrier.
5. **Plate never opens the wrong car.** `POST /barrier/exit/plate` never changes a session whose Known plate differs from the normalized input, nor a session whose plate status is `PENDING` or `UNREADABLE`.
6. **Entry independent of the queue.** For any publisher failure, `POST /entries` returns 201 with status `PARKED` and the session's plate status is never left `PENDING`.
7. **Publisher honesty.** `Publish` returns nil only if the broker confirmed the message.

Properties 1, 2, 3 and 5 are checked with table-driven and randomized-sequence tests over the in-memory fakes of `service/parking_test.go`; property 2 also with concurrent goroutines against the fake repository's conditional update.

## Error Handling

| Failure | Behaviour |
|---|---|
| RabbitMQ down, queue missing, nack, timeout, empty URL | Entry succeeds; plate `UNREADABLE`; `ENTRY` audit with `ocr_queued: false` and `queue_error` |
| Redis down or key missing at the exit | `MarkAsExited` already committed; `Increment` is best effort; the next read rebuilds the counter from PostgreSQL, where the session is already `EXITED` |
| DynamoDB down | Audit stays best effort, as today; the transition is not rolled back |
| PostgreSQL down | HTTP 500; nothing changes |
| Bucket creation fails at startup | Logged; the API still starts |

## Testing Strategy

- **Unit (Go):** publisher with an injected dialer/fake channel (confirmed, nacked, returned, timeout, reconnect after a closed channel, empty URL); service rules for every status and both exit routes; delete and plate-correction rules; counter rebuild; config parsing.
- **Handlers:** status codes and error codes of the two new routes; unchanged routes keep their responses.
- **Repository (existing integration style in `repository_test.go`):** migration `000003` up/down on legacy rows, `MarkAsExited`, `FindOccupyingByPlate` ordering.
- **Web (Vitest):** `ExitBarrierPage` outcomes, route redirects, payment message, dashboard badge.
- **End to end (manual, compose):** the seven steps of `docs/PLANO-TP2.md` section 4.2, with step 4 using a typed plate.

## Risks

| Risk | Mitigation |
|---|---|
| The Trabalho 1 AWS deployment stops working with the new API image | Accepted and documented; Trabalho 2 replaces it |
| A misread plate (`READ` but wrong) never matches at the exit | The ticket fallback; the cashier can correct the plate while `PARKED` or `PAID` |
| A `MANUAL` plate typed wrong matches another car's real plate | Only a `PAID` session can be opened, and only one per request; the audit records `method: plate` |
| `Increment` lost when Redis is down at the exit | The counter is rebuilt from PostgreSQL on the next read |
| The scratch API image cannot run a healthcheck | `service_started` for `web`; documented |
