# Requirements Document

## Introduction

This feature finishes the Trabalho 2 migration on the API side and adds the exit barrier described in `docs/PLANO-TP2.md` (Components 1, 2 and 4):

1. **Open-source backing services.** The Go API stops using Amazon SQS and the single `AWS_ENDPOINT_URL`. It publishes OCR tasks to RabbitMQ, stores photos in an S3-compatible storage (RustFS) and writes the audit trail to DynamoDB (DynamoDB Local or real AWS), each with its own endpoint. The Worker already consumes RabbitMQ (`docs/PLANO-TP2-WORKER.md`); this feature closes the photo -> queue -> plate loop.
2. **Two barriers and a decoupled payment.** Paying a ticket no longer frees the spot. The car is still inside the lot until it crosses the exit barrier, which opens only for a paid session, found by its plate or by its ticket ID. The spot goes back to the counter at the barrier.
3. **One compose for the whole system.** The `api` and `web` services join `infra/docker-compose.yaml`, and the frontend gains the exit-barrier page.

The rule of `docs/DECISOES.md` D20 stays untouched: the ticket identifies the car. No rule may require a read plate for a car to pay or to leave. The plate is only a shortcut at the exit barrier; the ticket always works.

Out of scope: the Kubernetes bonus (Component 5), the AWS deployment of Trabalho 1 (`infra/*.tf`, `user_data`), any change to the Worker, synchronous OCR at the exit barrier, a grace period between payment and exit, and authentication.

## Glossary

- **API**: The Go REST service in `api/`.
- **Worker**: The Python OCR consumer in `worker/` (unchanged by this feature).
- **Web**: The React + Vite frontend in `web/`, served by nginx, which proxies `/api/` to the API.
- **Event_Publisher**: The API component that publishes OCR tasks (`repository.EventPublisher`).
- **OCR_Queue**: The durable RabbitMQ queue `ocr-processamento-fila`. Its topology (with `-retry` and `-dlq`) is declared only by `infra/rabbitmq/definitions.json`.
- **Session_Message**: The JSON body `{"session_id": "<32 hex chars>", "s3_key": "photos/{session_id}_{filename}"}`.
- **Blob_Storage**: The S3-compatible storage for entry photos (RustFS locally, Amazon S3 otherwise).
- **Audit_Logger**: The API component that writes audit entries to the DynamoDB table `AuditoriaEstacionamento`.
- **Spots_Counter**: The Redis key `spots:available`, changed only by the API.
- **Session**: A row of the `sessions` table.
- **Session status**: Where the car is. `PARKED` (inside, not paid), `PAID` (inside, paid, allowed to leave), `EXITED` (left through the exit barrier).
- **Plate status**: How the plate reading went (`PENDING`, `READ`, `UNREADABLE`, `MANUAL`). Independent of the session status.
- **Occupying session**: A session with status `PARKED` or `PAID`. It holds one spot.
- **Ticket**: The session ID printed at the entry barrier.
- **Exit_Barrier**: The gate a car crosses to leave, driven by `POST /barrier/exit/plate` and `POST /barrier/exit/ticket`.
- **Known plate**: A `license_plate` whose plate status is `READ` or `MANUAL`.

## Requirements

### Requirement 1: Publishing OCR tasks to RabbitMQ

**User Story:** As a system operator, I want the API to publish OCR tasks to RabbitMQ, so that the Worker reads the plate of every entry photo without any AWS queue.

#### Acceptance Criteria

1. WHEN an entry is created, THE Event_Publisher SHALL publish one Session_Message to the OCR_Queue through the default exchange, with the queue name as routing key, `delivery_mode` 2 (persistent) and content type `application/json`.
2. THE Event_Publisher SHALL use publisher confirms and the `mandatory` flag, and SHALL report success only after the broker confirmed the message.
3. THE Event_Publisher SHALL check that the OCR_Queue exists with a passive declaration and SHALL NOT declare, change or delete any queue or exchange.
4. THE API SHALL read the broker URL from `RABBITMQ_URL` and the queue name from `RABBITMQ_QUEUE_NAME` (default `ocr-processamento-fila`).
5. THE `session_id` of every Session_Message SHALL be exactly 32 lowercase hexadecimal characters, as produced by the API's ID generator.
6. THE Event_Publisher SHALL bound each publication (connection included) to 5 seconds.
7. IF the connection or channel is closed, THEN THE Event_Publisher SHALL reconnect on the next publication instead of failing permanently.
8. THE API SHALL NOT log the credentials embedded in `RABBITMQ_URL`.
9. THE API SHALL contain no Amazon SQS client, configuration or dependency after this feature.

### Requirement 2: The queue never blocks the entry

**User Story:** As a driver, I want the entry barrier to open even when the queue is down, so that a broker failure never keeps me outside.

#### Acceptance Criteria

1. IF a publication fails for any reason (broker unreachable, queue missing, timeout, message not confirmed or returned), THEN THE API SHALL still return the created session with status `PARKED` and HTTP 201.
2. IF a publication fails, THEN THE API SHALL set the session's plate status to `UNREADABLE`, SHALL record `ocr_queued: false` and the error in the `ENTRY` audit entry, and SHALL log a warning.
3. IF `RABBITMQ_URL` is empty, THEN THE Event_Publisher SHALL report a failure on every publication, so that no session is left `PENDING` waiting for a Worker that will never receive it.
4. THE API SHALL start and serve requests while RabbitMQ is unreachable.

### Requirement 3: Independent endpoints for storage and audit

**User Story:** As a developer, I want each backing service to have its own endpoint, so that the API runs against local containers or real AWS by changing only environment variables.

#### Acceptance Criteria

1. THE API SHALL read the storage endpoint from `S3_ENDPOINT_URL` and the DynamoDB endpoint from `DYNAMODB_ENDPOINT_URL`.
2. WHERE `S3_ENDPOINT_URL` is set, THE API SHALL use path-style addressing and that endpoint for Blob_Storage.
3. WHERE an endpoint variable is empty, THE API SHALL use the default AWS endpoint of that service.
4. THE API SHALL ignore `AWS_ENDPOINT_URL` and `SQS_QUEUE_URL`.
5. WHERE `S3_ENDPOINT_URL` is set, THE API SHALL create the bucket `S3_BUCKET_NAME` at startup when it does not exist, and SHALL treat "already exists" as success.
6. WHERE `S3_ENDPOINT_URL` is empty, THE API SHALL NOT try to create the bucket.
7. THE variable names SHALL be the ones already used by the Worker and by `Taskfile.yaml` (`S3_ENDPOINT_URL`, `DYNAMODB_ENDPOINT_URL`, `RABBITMQ_URL`, `RABBITMQ_QUEUE_NAME`).

### Requirement 4: Paying keeps the spot

**User Story:** As a parking operator, I want a paid car to keep its spot until it leaves, so that the free-spot display matches the cars that are really inside.

#### Acceptance Criteria

1. WHEN a `PARKED` session is paid, THE API SHALL set its status to `PAID`, its `amount_paid` and its `paid_at`, and SHALL NOT change the Spots_Counter.
2. WHEN a session is paid, THE API SHALL leave `exited_at` empty.
3. THE payment route SHALL remain `POST /exits/{id}/pay` and its audit action SHALL remain `EXIT_PAYMENT`, with `status` and `amount_paid` in the details.
4. IF the session is not `PARKED`, THEN THE payment SHALL be rejected with HTTP 409 and SHALL change nothing.
5. THE payment SHALL work for any plate status, including `PENDING` and `UNREADABLE`.

### Requirement 5: Exit barrier by ticket

**User Story:** As a driver, I want the exit barrier to open with my paper ticket, so that I can always leave after paying, whatever happened to the plate reading.

#### Acceptance Criteria

1. THE API SHALL expose `POST /barrier/exit/ticket` accepting the JSON body `{"ticket_id": "<session id>"}`.
2. WHEN the ticket belongs to a `PAID` session, THE API SHALL set the status to `EXITED` and `exited_at` to the current time, increment the Spots_Counter by one, write the audit action `EXIT_BARRIER_OPENED` and return HTTP 200 with the session.
3. IF the ticket belongs to a `PARKED` session, THEN THE API SHALL return HTTP 409 with the error code `payment_pending` and SHALL change nothing.
4. IF the ticket belongs to an `EXITED` session, THEN THE API SHALL return HTTP 409 with the error code `already_exited` and SHALL change nothing.
5. IF no session has that ID, THEN THE API SHALL return HTTP 404.
6. IF the body is not valid JSON or `ticket_id` is missing or empty, THEN THE API SHALL return HTTP 400.
7. THE exit by ticket SHALL work for any plate status.

### Requirement 6: Exit barrier by plate

**User Story:** As a driver, I want the exit barrier to open when it recognizes my plate, so that I leave without handling the ticket.

#### Acceptance Criteria

1. THE API SHALL expose `POST /barrier/exit/plate` accepting the JSON body `{"plate": "<plate>"}`.
2. THE API SHALL normalize the plate with the same rules as the cashier's plate correction (upper case, no spaces or hyphens, Mercosul formats) and SHALL return HTTP 400 for an invalid plate.
3. THE API SHALL look for Occupying sessions whose Known plate equals the normalized plate.
4. WHEN at least one matching session is `PAID`, THE API SHALL open the barrier for the matching `PAID` session with the oldest `paid_at`, with the same effects as Requirement 5.2, and SHALL add `"method": "plate"` to the audit details.
5. IF every matching session is `PARKED`, THEN THE API SHALL return HTTP 409 with the error code `payment_pending` and SHALL change nothing.
6. IF no Occupying session has that Known plate, THEN THE API SHALL return HTTP 404 with a message telling the driver to use the ticket.
7. THE API SHALL NOT run OCR, accept a photo or wait for the Worker in this route.
8. THE exit by plate SHALL never be the only way out: every session that this route cannot open SHALL remain openable by Requirement 5.

### Requirement 7: The spot is released exactly once

**User Story:** As a parking operator, I want each car to free exactly one spot when it leaves, so that the counter never drifts.

#### Acceptance Criteria

1. THE transition from `PAID` to `EXITED` SHALL be a single conditional update (`WHERE status = 'PAID'`), and THE API SHALL increment the Spots_Counter only when that update changed a row.
2. WHEN two exit requests for the same session race, THE API SHALL open the barrier for one and answer the other with `already_exited`, incrementing the Spots_Counter once.
3. THE count used to rebuild the Spots_Counter from PostgreSQL SHALL be the number of Occupying sessions (`PARKED` and `PAID`).
4. WHEN an Occupying session is deleted (`DELETE /sessions/{id}`), THE API SHALL increment the Spots_Counter by one; WHEN an `EXITED` session is deleted, THE API SHALL NOT change it.
5. THE Spots_Counter SHALL never exceed `TOTAL_PARKING_SPOTS` minus the number of Occupying sessions as a result of the operations of this feature.
6. THE entry SHALL keep reserving the spot atomically and answering HTTP 409 when the lot is full (unchanged).

### Requirement 8: Schema migration

**User Story:** As a developer, I want the database to move to the new status model without manual steps, so that existing data stays consistent with the counter.

#### Acceptance Criteria

1. THE API SHALL add a migration `000003` that adds the nullable column `paid_at` to `sessions`.
2. THE migration SHALL convert every existing `PAID` session to `EXITED`, copying `exited_at` into `paid_at`, because under the previous rule those cars already left and already gave their spot back.
3. THE migration SHALL have a `down` script that restores the previous status model.
4. AFTER the migration, the Spots_Counter SHALL be valid without being deleted or rebuilt.
5. THE session JSON SHALL expose `paid_at` (omitted when empty) next to `exited_at`.

### Requirement 9: Listing and auditing

**User Story:** As a cashier, I want to see which cars are inside and what happened to each ticket, so that I can help a driver at any step.

#### Acceptance Criteria

1. `GET /sessions?status=` SHALL accept `PARKED`, `PAID` and `EXITED`.
2. THE audit trail SHALL contain `ENTRY`, `EXIT_PAYMENT` and `EXIT_BARRIER_OPENED` for a complete stay, in that order.
3. THE `EXIT_BARRIER_OPENED` details SHALL contain `status`, `method` (`plate` or `ticket`) and `license_plate` when known.
4. THE cashier's plate correction (`PATCH /sessions/{id}`) SHALL be allowed for `PARKED` and `PAID` sessions, so that a paid car can still gain a plate before reaching the barrier, and SHALL be rejected for `EXITED` sessions.

### Requirement 10: Frontend

**User Story:** As a driver or operator, I want one screen for each step of the stay, so that the flow of the lot is visible end to end.

#### Acceptance Criteria

1. THE Web SHALL serve the entry totem at `/cancela-entrada`, the payment terminal at `/pagamento`, the exit barrier at `/cancela-saida`, the dashboard at `/` and the audit trail at `/auditoria`.
2. THE previous paths `/entrada` and `/saida` SHALL redirect to `/cancela-entrada` and `/pagamento`.
3. THE exit-barrier page SHALL offer the plate first and the ticket ID as fallback, and SHALL show "barrier open" on HTTP 200, "pay at the cashier" on `payment_pending`, "already left" on `already_exited` and "not found, use the ticket" on HTTP 404.
4. AFTER a payment, THE payment page SHALL tell the driver to go to the exit barrier and SHALL NOT state that the spot was freed.
5. THE dashboard SHALL list `PARKED` and `PAID` sessions as cars inside the lot, distinguishing the paid ones.
6. THE audit page SHALL label `EXIT_BARRIER_OPENED`.
7. THE Web types SHALL include the status `EXITED` and the field `paid_at`.

### Requirement 11: Compose and automation

**User Story:** As an evaluator, I want the whole system to start with one command, so that I can check the rubric without installing anything but Docker.

#### Acceptance Criteria

1. `infra/docker-compose.yaml` SHALL define the service `api` (build `../api`, port 8080) depending on `postgres`, `redis`, `rabbitmq`, `rustfs` and `dynamodb-local` with `condition: service_healthy`, and on `createbuckets` and `createtables` with `condition: service_completed_successfully`.
2. `infra/docker-compose.yaml` SHALL define the service `web` (build `../web`, host port 3000) with `API_UPSTREAM=http://api:8080`, depending on `api`.
3. `docker compose up -d --build` in `infra/` SHALL bring up every service, and the flow entry -> OCR -> payment -> exit SHALL work from `http://localhost:3000`.
4. `Taskfile.yaml` SHALL provide `up`, `down` and `logs` for that compose and a `test` task that runs the API, Worker and Web tests.
5. THE `dev:api` task SHALL keep working against the compose services.

### Requirement 12: Tests and documentation

**User Story:** As a maintainer, I want the new rules covered by tests and written down, so that the next change does not break them silently.

#### Acceptance Criteria

1. `go test ./...` SHALL cover: publication (confirmed, failed, empty URL, reconnection), payment without counter change, exit by ticket and by plate for every status, the single release under a race, the delete rule and the counter rebuild.
2. THE Web tests SHALL cover the exit-barrier page for the four outcomes of Requirement 10.3.
3. `AGENTS.md` (root, `api/`, `web/`), `README.md`, `docs/ARQUITETURA.md` and `docs/PLANO-TP2.md` SHALL describe the new flow, routes, variables and status model.
4. `docs/DECISOES.md` SHALL record the two-barrier decision and the API's move to RabbitMQ as new entries.
