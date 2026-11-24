# Implementation Plan: API on RabbitMQ and the Exit Barrier

## Overview

The work is ordered so that each phase leaves the system working. Phase 1 is a pure infrastructure swap behind existing interfaces and can be merged alone: it restores the full local flow (photo -> API -> RabbitMQ -> Worker -> plate). Phases 2 to 4 add the exit barrier from the database up to the Web. Phase 5 puts everything in the compose and verifies the plan's end-to-end steps.

Tests are written with each task. `go test ./...`, `task test:web` and `task lint:web` must pass at the end of every phase.

## Tasks

- [x] 1. API on RabbitMQ, RustFS and DynamoDB Local (no business rule changes)
  - [x] 1.1 Configuration
    - Remove `AWSEndpointURL` and `SQSQueueURL`; add `S3EndpointURL`, `DynamoDBEndpointURL`, `RabbitMQURL`, `RabbitMQQueueName` (default `ocr-processamento-fila`)
    - Update `config_test.go`
    - _Requirements: 1.4, 3.1, 3.3, 3.4, 3.7_
  - [x] 1.2 `repository/rabbitmq.go`
    - Add `github.com/rabbitmq/amqp091-go`; implement `RabbitMQEventPublisher` (lazy connection, passive declaration, confirms, `mandatory`, persistent, 5 s bound, reconnect after failure, `Close`)
    - URL redaction helper for logs
    - _Requirements: 1.1, 1.2, 1.3, 1.5, 1.6, 1.7, 1.8, 2.3, 2.4_
  - [x] 1.3 Publisher tests with an injected dialer
    - Confirmed, nacked, returned, timeout, reconnection after a closed channel, empty URL, queue missing
    - _Requirements: 1.2, 1.7, 2.3, 12.1_
  - [x] 1.4 Wire `main.go`
    - S3 and DynamoDB clients with their own endpoints; remove `NewSQSClient`; provide the RabbitMQ publisher; close it on stop
    - Delete `repository/sqs.go` and drop the SQS module from `go.mod`
    - _Requirements: 1.9, 3.1, 3.2, 3.3_
  - [x] 1.5 `EnsureBucket` at startup when `S3_ENDPOINT_URL` is set
    - _Requirements: 3.5, 3.6_
  - [x] 1.6 Entry tests for the degraded path
    - Publisher failure keeps HTTP 201, `PARKED`, plate `UNREADABLE`, `ocr_queued: false`
    - _Requirements: 2.1, 2.2_
  - [x] 1.7 Checkpoint: `task dev:api` against the compose services plus the `worker` service
    - Send a photo through `POST /entries`; the session reaches `plate_status = READ`; the queues end empty
    - Done 2026-11-16 with the API image running on the compose network (no local Go toolchain): readable photo -> `READ` `LSN4I49`, covered plate -> `UNREADABLE`, counter 50 -> 48, the three queues empty

- [x] 2. Status model and persistence
  - [x] 2.1 Migration `000003_exit_barrier` (up and down)
    - `paid_at`, legacy `PAID` -> `EXITED`, partial index on `license_plate`
    - _Requirements: 8.1, 8.2, 8.3, 8.4_
  - [x] 2.2 Model: `StatusExited`, `PaidAt`
    - _Requirements: 8.5_
  - [x] 2.3 Repository
    - `MarkAsPaid` writes `paid_at`; new `MarkAsExited` and `FindOccupyingByPlate`; `UpdatePlate` and `CountActive` include `PAID`; `sessionColumns` gains `paid_at`
    - _Requirements: 4.1, 4.2, 6.3, 7.1, 7.3, 9.4_
  - [x] 2.4 Repository tests
    - Migration over legacy rows, conditional updates, plate lookup ordering
    - _Requirements: 8.2, 7.1, 12.1_

- [x] 3. Exit barrier in the service and the handlers
  - [x] 3.1 `PayExit` stops incrementing the counter
    - _Requirements: 4.1, 4.3, 4.4, 4.5_
  - [x] 3.2 `ExitBarrierByTicket`, `ExitBarrierByPlate`, `openBarrier` and the new errors
    - _Requirements: 5.2, 5.3, 5.4, 5.5, 5.7, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 7.1, 7.2, 9.3_
  - [x] 3.3 `DeleteSession` and `UpdatePlate` rules for `PAID` and `EXITED`
    - _Requirements: 7.4, 9.4_
  - [x] 3.4 Handlers `POST /barrier/exit/ticket` and `POST /barrier/exit/plate` with error codes
    - _Requirements: 5.1, 5.6, 6.1, 6.2, 9.1_
  - [x] 3.5 Service and handler tests
    - Every status for both routes, repeated plates, plate statuses `PENDING`/`UNREADABLE`, body validation
    - _Requirements: 12.1_
  - [x]* 3.6 Property tests
    - Spot conservation over random operation sequences (Property 1); single release under concurrent exits (Property 2); no exit without payment (Property 3); plate never opens the wrong car (Property 5)
    - _Requirements: 7.1, 7.2, 7.5_
    - Done 2026-11-17: 200 seeded sequences of 60 operations (`TestSpotConservation_RandomSequences`), 20 concurrent exits (`TestExit_ConcurrentRequestsReleaseTheSpotOnce`, also under `-race`), and table tests for Property 5 (`TestExitByPlate_NotFound`, `TestExitByPlate_RepeatedPlate`)
  - [x] 3.7 Checkpoint on the compose (API image on the compose network, migration 000003 applied over the phase 1 data)
    - Two entries reserve two spots; the Worker reads one plate and marks the other `UNREADABLE`; both refused at the barrier before paying (`payment_pending`); payment keeps the counter; exit by plate frees one spot and a second exit answers `already_exited`; an unknown plate answers `plate_not_found`; the unreadable car leaves by ticket; the counter is back where it started; audit `ENTRY` -> `OCR_*` -> `EXIT_PAYMENT` -> `EXIT_BARRIER_OPENED` with `method` `plate`/`ticket`

- [x] 4. Frontend
  - [x] 4.1 Types and API client (`EXITED`, `paid_at`, `EXIT_BARRIER_OPENED`, `exitByPlate`, `exitByTicket`, error `code`)
    - _Requirements: 10.7_
  - [x] 4.2 `ExitBarrierPage` with its tests
    - _Requirements: 10.3, 12.2_
  - [x] 4.3 Routes, redirects and navigation
    - _Requirements: 10.1, 10.2_
  - [x] 4.4 Payment message, dashboard listing with the paid badge, audit label
    - _Requirements: 10.4, 10.5, 10.6_
  - [x] 4.5 Checkpoint in the browser (Vite dev server against the API on the compose)
    - `/saida` redirects to `/pagamento`; paying shows "Siga para a cancela de saída" with `paid_at`; the dashboard lists 7 cars inside (1 paid) and 43 free spots; the exit totem refuses an unpaid ticket typed by its 8-character prefix ("Pagamento não localizado"), opens for the paid car's plate while three unpaid sessions share it; the audit page shows "Saída pela cancela · Cancela aberta pela placa"
    - The exit totem resolves the 8-character prefix printed on the ticket to the full ID through `GET /sessions?status=ALL` (unique match only), because the API takes the full ID

- [x] 5. Compose, automation and verification
  - [x] 5.1 Services `api` and `web` in `infra/docker-compose.yaml`
    - _Requirements: 11.1, 11.2_
  - [x] 5.2 Tasks `up`, `down`, `logs`, `test`; keep `dev:api` working
    - _Requirements: 11.4, 11.5_
    - `infra:up` now starts only the dependencies (for `dev:api`/`dev:worker`); `up` starts everything with `--build`. `dev:api` and the compose `api` both use port 8080: stop the container before running `dev:api`
  - [x] 5.3 End-to-end verification (`docs/PLANO-TP2.md`, section 4.2)
    - Entry with photo; OCR by the Worker; payment keeps the counter; exit by plate frees one spot; exit by ticket with an unreadable plate; unpaid car refused; audit trail complete; full lot answers 409
    - _Requirements: 11.3, 9.2_
    - Done 2026-11-17 on a fresh stack (`docker compose -p tp2fresh`, new volumes): every service up, the API created the schema on an empty database, `http://localhost:3000` and its SPA routes answer 200; the 18 checks of the flow passed through the web's nginx (`/api/`); with `spots:available` at 0 the entry answered 409
    - Found and fixed: the RabbitMQ healthcheck (`rabbitmq-diagnostics ping`) turned healthy before port 5672 accepted connections, so the Worker logged "connection refused" at boot and the API's first entry could lose its plate. It is now `check_port_connectivity`; after the change neither logs a connection error on boot
  - [x] 5.4 Broker outage check
    - Stop `rabbitmq`, create an entry, confirm HTTP 201 and plate `UNREADABLE`; start it again and confirm the next entry is queued
    - Done 2026-11-16: with the broker stopped the entry answered 201 in 5.2 s, `UNREADABLE`, `ENTRY` audit with `ocr_queued: false`; once the broker listened again the next entry was queued and read
    - _Requirements: 2.1, 2.2, 1.7_

- [x] 6. Documentation
  - [x] 6.1 `AGENTS.md` (root, `api/`, `web/`) and `README.md`: flow, routes, variables, status model, changelog
    - _Requirements: 12.3_
  - [x] 6.2 `docs/DECISOES.md`: new entries for the two barriers and for the API on RabbitMQ
    - _Requirements: 12.4_
    - Done as D21 (two barriers) and D22 (API on RabbitMQ, one endpoint per service); D13, D14, D15 and D20 corrected where they described the plan instead of the code
  - [x] 6.3 `docs/ARQUITETURA.md` and `docs/PLANO-TP2.md` (state of section 5, decisions closed by this spec)
    - _Requirements: 12.3_
  - [x] 6.4 Screenshots for the README (queue in the RabbitMQ panel, Worker log, exit barrier), closing item 7.3 of `docs/PLANO-TP2-WORKER.md`
    - Done 2026-11-22: `docs/evidencias/` (cashier with the fare, audit, dashboard, exit barrier, RabbitMQ queues, RustFS bucket, `docker compose ps` and the Worker log), linked from the README

## Notes

- Tasks marked with `*` are optional.
- Phase 1 can be its own pull request; phases 2 to 4 depend on each other and should land together.
- The Worker is not changed by any task. Its `UPDATE ... WHERE plate_status = 'PENDING'` does not look at `status`, so a plate read after the payment or after the exit is still recorded.
- Deploying to AWS (`infra/*.tf`) is out of scope and stops working with the new API image.
