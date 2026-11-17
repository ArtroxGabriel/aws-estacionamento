# Requirements Document

## Introduction

The Python OCR Worker is the asynchronous background component of the parking-lot management system (Trabalho Prático de Cloud da UFC). It continuously consumes vehicle-entry events from a RabbitMQ queue, downloads the corresponding vehicle photo from an S3-compatible object storage, extracts the license plate through image pre-processing and Tesseract OCR, records the plate in the relational session record (PostgreSQL) and writes an immutable audit entry to DynamoDB. The Worker only *enriches* the session: the car is already parked and holding its spot when the message arrives (`docs/DECISOES.md`, D20).

> Revised 2026-11-16 for Trabalho 2: the transport moved from Amazon SQS to RabbitMQ, the S3 and DynamoDB endpoints became independent (local containers or real AWS) and the Amazon Rekognition engine was removed (`docs/PLANO-TP2-WORKER.md`).

Each backing service has its own endpoint, driven exclusively by environment variables. The Worker must be resilient to OCR failures, unreadable plates, message redelivery, poison messages, downstream connection failures, and process shutdown signals, while preserving the exactness of the shared data contracts defined by the API component.

## Glossary

- **Worker**: The Python 3.14 asynchronous process consuming the RabbitMQ queue and orchestrating the OCR pipeline.
- **Consumer**: The component of the Worker that owns the RabbitMQ transport: connection, consumption, acknowledgement, retry queue, dead-letter queue, reconnection and shutdown (`consumer.py`).
- **Message_Processor**: The component that applies the business rules to one message body and delivery attempt and reports an outcome (`processor.py`).
- **Poller**: Name kept by older text for the Worker's message handling as a whole, i.e. the Consumer together with the Message_Processor.
- **OCR_Processor**: The component that pre-processes images and extracts raw text via Tesseract (`ocr/processor.py`).
- **Plate_Normalizer**: The component that normalizes raw OCR text into a valid plate format (`ocr/clean.py`).
- **S3_Connector**: The storage component that downloads vehicle photos from Amazon S3 (`storage/`).
- **Session_Repository**: The storage component that reads and updates session records in Amazon RDS (PostgreSQL) (`storage/`).
- **Spots_Counter**: The storage component that mutates the available-spots counter in Amazon ElastiCache (Redis) (`storage/`).
- **Audit_Logger**: The storage component that writes immutable audit entries to Amazon DynamoDB (`storage/`).
- **Config_Loader**: The component that loads and validates environment-driven configuration at startup.
- **OCR_Queue** (called `SQS_Queue` in older text): The RabbitMQ durable queue `ocr-processamento-fila`.
- **Retry_Queue**: The durable queue `ocr-processamento-fila-retry` (message TTL 30 s, dead-letters back into the OCR_Queue); messages wait there between attempts.
- **Dead_Letter_Queue**: The durable queue `ocr-processamento-fila-dlq`, without consumers.
- **Attempt**: The 1-based delivery attempt of a message, carried in the `x-attempt` header (absent means 1).
- **Session_Message**: The JSON payload carried in the RabbitMQ message body, of shape `{"session_id": "<hex_32_chars>", "s3_key": "photos/{session_id}_{filename}"}`.
- **Session_Record**: A row in the `sessions` table (`id`, `license_plate`, `status`, `s3_photo_key`, `entered_at`, `exited_at`, `amount_paid`).
- **Spots_Key**: The Redis key `spots:available` holding the integer count of available parking spots.
- **Audit_Table**: The DynamoDB table `AuditoriaEstacionamento`.
- **Audit_Entry**: A DynamoDB item with `id` = `<session_id>#<timestamp_nano>`, `action`, `entity_id`, `timestamp`, and `details`.
- **Mercosul_Format**: A license-plate format matching the pattern `ABC1D23` (three letters, one digit, one letter, two digits).
- **Old_Format**: A legacy Brazilian license-plate format matching the pattern `ABC-1234` (three letters, hyphen, four digits).
- **Foreign Mercosul formats** (added 2026-10-03, see `docs/DECISOES.md`, D8): Argentina `AB123CD` (always) and `ABC123` (only with `ARGENTINA` read, as a whole line), Paraguay `ABCD123` (only with `PARAGUAY` read); Uruguay's Mercosul plate shares the Old_Format pattern. Requirement 5 applies to them for exact matches only; positional corrections stay Brazilian.
- **PROCESSING**: The initial session status set by the API on entry.
- **PARKED**: The session status set by the Worker after successful OCR extraction.
- **Poison_Message**: A message that repeatedly fails processing and exceeds the configured redelivery threshold.

## Requirements

### Requirement 1: Continuous RabbitMQ Consumption

**User Story:** As a system operator, I want the Worker to continuously consume entry events from the RabbitMQ queue, so that vehicle photos are processed automatically without manual intervention.

#### Acceptance Criteria

1. WHILE the Worker is running, THE Consumer SHALL consume the OCR_Queue with manual acknowledgements and `prefetch_count` 1, so that each Worker instance holds at most one unacknowledged message.
2. WHEN a message is delivered, THE Consumer SHALL hand its body and Attempt to the Message_Processor and apply the returned outcome.
3. WHEN a message has been processed successfully (or is terminal/idempotent), THE Consumer SHALL acknowledge it (`basic_ack`).
4. IF the connection to the broker is lost or cannot be established, THEN THE Consumer SHALL reconnect with an exponential back-off of 1 second doubling up to no more than 30 seconds, SHALL continue running without terminating, and SHALL leave unacknowledged messages for the broker to redeliver.
5. THE Consumer SHALL read the broker URL from `RABBITMQ_URL` and the queue name from `RABBITMQ_QUEUE_NAME` (default `ocr-processamento-fila`); the Retry_Queue and Dead_Letter_Queue names are that name followed by `-retry` and `-dlq`.
6. IF `RABBITMQ_URL` is absent, empty or not an `amqp`/`amqps` URL at startup, THEN THE Worker SHALL terminate startup and produce an error indication identifying the configuration.
7. WHEN the Consumer connects, THE Consumer SHALL check with passive declarations that the OCR_Queue, the Retry_Queue and the Dead_Letter_Queue exist, and IF any is missing THEN THE Worker SHALL exit with status 2 and an error naming the queue. The queues are declared by `infra/rabbitmq/definitions.json`; the Worker never declares them.
8. THE Consumer SHALL use a connection heartbeat of 120 seconds, larger than the worst-case processing time of one message.

### Requirement 2: Message Parsing and Validation

**User Story:** As a developer, I want the Worker to parse and validate the incoming message payload, so that only well-formed events enter the OCR pipeline.

#### Acceptance Criteria

1. WHEN a message is received, THE Poller SHALL parse the RabbitMQ message body, whose size SHALL NOT exceed 256 KB, as a Session_Message JSON object without unwrapping any SNS envelope.
2. WHEN a Session_Message is parsed successfully, THE Poller SHALL extract the `session_id` and `s3_key` field values as UTF-8 strings.
3. IF the message body is not valid JSON, THEN THE Poller SHALL classify the message as a Poison_Message, record a parsing error indicating malformed JSON, and leave the original message body unmodified.
4. IF the parsed Session_Message is missing the `session_id` field, is missing the `s3_key` field, or contains either field as an empty string, THEN THE Poller SHALL classify the message as a Poison_Message and record a validation error indicating the specific missing or empty field.
5. IF the `session_id` value does not match a string of exactly 32 characters composed only of hexadecimal digits (0-9, a-f, A-F), THEN THE Poller SHALL classify the message as a Poison_Message and record a validation error indicating an invalid `session_id` format.
6. IF the `s3_key` value is an empty string or exceeds 1024 characters, THEN THE Poller SHALL classify the message as a Poison_Message and record a validation error indicating an invalid `s3_key` format.

### Requirement 3: Vehicle Photo Retrieval from S3

**User Story:** As a developer, I want the Worker to download the original vehicle photo from S3, so that the image is available for OCR extraction.

#### Acceptance Criteria

1. WHEN a valid Session_Message is received, THE S3_Connector SHALL download the object identified by the `s3_key` from the bucket named in the `S3_BUCKET_NAME` environment variable within 10 seconds.
2. IF the `S3_BUCKET_NAME` environment variable is unset or empty, OR the `s3_key` field is absent or empty in the Session_Message, THEN THE S3_Connector SHALL return a retrieval error to the Poller indicating invalid retrieval parameters and SHALL NOT attempt the download.
3. THE S3_Connector SHALL hold the downloaded image content in memory as a byte stream up to a maximum size of 10 MB.
4. IF the downloaded object exceeds 10 MB, THEN THE S3_Connector SHALL abort the download, release any partially held content, and return a retrieval error to the Poller indicating the size limit was exceeded.
5. WHEN image processing for a message completes with either success or failure, THE S3_Connector SHALL release the in-memory image content associated with that message.
6. IF the object identified by the `s3_key` does not exist in the bucket, THEN THE S3_Connector SHALL return a retrieval error to the Poller indicating the object was not found.
7. IF the download fails due to S3 unavailability or a network error after a maximum of 3 retry attempts, THEN THE S3_Connector SHALL return a retrieval error to the Poller indicating the retrieval failure.

### Requirement 4: Image Pre-Processing and OCR Extraction

**User Story:** As a system operator, I want the Worker to pre-process the photo and run OCR, so that the license plate text can be extracted from the image.

#### Acceptance Criteria

1. WHEN an image byte stream is available, THE OCR_Processor SHALL locate plates both by the Mercosul blue band and by plate shape (a rectangle of plate proportions, straightened when tilted, containing a row of at least 5 character-like blobs, which covers Old_Format plates and Mercosul plates on blue cars) and, for each located character strip and for the whole image, apply rescaling (strip to a fixed height; whole image so its longer side is between 1000 and 2000 px), grayscale conversion, and binary thresholding (with the threshold chosen per image by Otsu's method), in that order, before invoking OCR; text from located strips SHALL come first, the whole image SHALL be skipped when a located strip already yields an exact plate, and the result SHALL indicate whether a Mercosul plate was located.
2. WHEN pre-processing completes, THE OCR_Processor SHALL run Tesseract OCR on the pre-processed image and produce raw text within 10 seconds per image.
3. WHEN Tesseract OCR produces raw text, THE OCR_Processor SHALL return the raw text to the Poller.
4. IF the image byte stream cannot be decoded into a valid image, THEN THE OCR_Processor SHALL return an image-decoding error to the Poller indicating the decode failure, and SHALL NOT invoke Tesseract OCR.
5. IF Tesseract OCR fails to produce any text output, THEN THE OCR_Processor SHALL return an OCR-extraction error to the Poller indicating that no text was extracted.
6. IF OCR processing for a single image does not complete within 10 seconds, THEN THE OCR_Processor SHALL abort the operation and return a timeout error to the Poller.

### Requirement 5: License Plate Normalization

**User Story:** As a system operator, I want the Worker to normalize the extracted plate text into a standard format, so that stored plates are consistent and valid.

#### Acceptance Criteria

1. WHEN raw OCR text is provided, THE Plate_Normalizer SHALL, for each line separately, remove all characters that are not ASCII letters (A-Z, a-z) or digits (0-9), convert all remaining letters to uppercase, remove the words `BRASIL` and `MERCOSUL`, and search the result for the first 7-character window matching the Mercosul_Format or Old_Format pattern; lines SHALL never be joined, so that unrelated OCR text cannot form a plate.
2. WHERE several lines hold an exact plate, the most frequent one SHALL be chosen (the earliest on a tie). WHERE the matched window is in the Mercosul_Format pattern (exactly 7 characters in the sequence letter-letter-letter-digit-letter-digit-digit), THE Plate_Normalizer SHALL return the plate in Mercosul_Format.
3. WHERE the matched window is in the Old_Format pattern (exactly 7 characters in the sequence letter-letter-letter-digit-digit-digit-digit), THE Plate_Normalizer SHALL return the plate in Old_Format.
4. IF no 7-character window of the normalized text matches the Mercosul_Format or the Old_Format pattern, THEN THE Plate_Normalizer SHALL return an unreadable-plate result that indicates normalization failed and SHALL NOT return a partial or padded plate value.
5. WHEN raw OCR text is empty or contains no letters or digits after removing disallowed characters, THE Plate_Normalizer SHALL return an unreadable-plate result.
6. WHEN a license plate value already in Mercosul_Format or Old_Format is provided as input, THE Plate_Normalizer SHALL return that same plate value unchanged (idempotence).
7. IF no window matches either pattern exactly, THEN THE Plate_Normalizer SHALL try each window against the Mercosul_Format and Old_Format position templates, replacing commonly confused characters with the character kind the position requires (e.g. `O` -> `0` in a digit position, `1` -> `I` in a letter position), with at most 2 replacements per plate, choosing the window with the fewest replacements; WHERE the OCR text contains `BRASIL` or `MERCOSUL`, only the Mercosul_Format template SHALL be used; WHERE the OCR_Processor located a Mercosul plate, only the Mercosul_Format template SHALL be used with at most 3 replacements.

### Requirement 6: Plate Recording in RDS

> Revisado em 2026-11-16 (`docs/DECISOES.md`, D20): o ticket identifica o carro. A sessão já nasce `PARKED`; o Worker grava apenas a leitura da placa em `plate_status`.

**User Story:** As a system operator, I want the Worker to record the plate read from the entry photo, so that the session carries it as evidence and as a shortcut to find the ticket, without the reading ever deciding whether the vehicle is parked.

#### Acceptance Criteria

1. WHEN a plate is successfully normalized, THE Session_Repository SHALL update the Session_Record identified by `session_id`, setting `license_plate` to the normalized plate and `plate_status` to `READ`.
2. THE Session_Repository SHALL read the RDS connection string from the `DATABASE_URL` environment variable.
3. IF no Session_Record exists with the given `session_id`, THEN THE Session_Repository SHALL report that nothing was updated and SHALL NOT create a new Session_Record.
4. THE Session_Repository SHALL NOT modify the Session_Record `status` under any circumstance; `status` is owned by the API.
5. IF the update operation fails due to an RDS connection or query error, THEN THE Session_Repository SHALL return an error to the Poller and SHALL leave the existing `license_plate` and `plate_status` values unchanged.
6. IF the existing Session_Record has a `plate_status` other than `PENDING` (already `READ` or `UNREADABLE`, or `MANUAL` because the cashier typed the plate), THEN THE Session_Repository SHALL leave the Session_Record unchanged, so that a plate typed by a person is never overwritten by the OCR.
7. THE Session_Repository SHALL record the plate regardless of the Session_Record `status`, so that a vehicle that paid before its photo was read still has its plate recorded.

### Requirement 7: No Spots Counter in the Worker

> Revisado em 2026-11-16 (D20). Substitui "Atomic Available-Spots Decrement in Redis".

**User Story:** As a system operator, I want the spot to be taken when the vehicle enters, not when its plate is read, so that availability is accurate even when the OCR is slow, wrong or down.

#### Acceptance Criteria

1. THE Worker SHALL NOT read or modify the Spots_Key; the API reserves the spot atomically while handling `POST /entries`.
2. THE Worker SHALL start without a `REDIS_URL` environment variable and SHALL ignore it when present.
3. THE Worker SHALL have no Redis dependency.

### Requirement 8: Immutable Audit Logging in DynamoDB

**User Story:** As a compliance reviewer, I want every OCR processing event recorded in an immutable audit log, so that all state changes are traceable.

#### Acceptance Criteria

1. WHEN a Session_Record is updated to `PARKED`, THE Audit_Logger SHALL write exactly one Audit_Entry to the Audit_Table with `action` equal to `OCR_PROCESSING` within 5 seconds of the update completing.
2. WHEN the Audit_Logger writes an Audit_Entry, THE Audit_Logger SHALL set the `id` field to the value `<session_id>#<timestamp_nano>`, where `timestamp_nano` is the Unix epoch time in nanoseconds at write.
3. WHEN the Audit_Logger writes an Audit_Entry, THE Audit_Logger SHALL include the identified license plate and the `session_id` in the Audit_Entry.
4. IF an Audit_Entry with an `id` equal to an already-persisted Audit_Entry `id` would be written, THEN THE Audit_Logger SHALL reject the write and preserve the existing Audit_Entry unchanged.
5. IF the write of an Audit_Entry to the Audit_Table fails, THEN THE Audit_Logger SHALL retry the write up to a maximum of 3 attempts and, if all attempts fail, record an error indication identifying the affected `session_id` without altering the Session_Record.
6. THE Audit_Logger SHALL read the DynamoDB table name from the `DYNAMODB_TABLE_NAME` environment variable, and IF the `DYNAMODB_TABLE_NAME` environment variable is unset or empty, THEN THE Audit_Logger SHALL fail initialization with an error indication that the table name is missing.

### Requirement 9: Message Acknowledgement After Processing

**User Story:** As a system operator, I want messages removed from the queue only after their outcome is safely recorded, so that failures are retried and nothing is lost.

#### Acceptance Criteria

1. WHEN the RDS plate update and the DynamoDB audit write both complete successfully for a message, THE Consumer SHALL acknowledge that message.
2. IF any step of the pipeline fails for a message and the Attempt is lower than 3, THEN THE Consumer SHALL publish an unchanged copy of the body to the Retry_Queue with the header `x-attempt` set to the Attempt plus one, as a persistent message, wait for the broker's publisher confirmation, and only then acknowledge the original; THE Worker SHALL log an error entry indicating which step failed.
3. IF a message fails processing on Attempt 3, THEN THE Message_Processor SHALL record an audit entry indicating the message exhausted its retries, and THE Consumer SHALL publish the body to the Dead_Letter_Queue with the headers `x-attempt` and `x-failure-reason`, wait for the confirmation, and then acknowledge the original.
4. IF publishing to the Retry_Queue or the Dead_Letter_Queue is not confirmed (unroutable, refused, connection lost), THEN THE Consumer SHALL NOT acknowledge the original, so that the broker redelivers it.
5. IF the Worker dies between a confirmed publication and the acknowledgement, THEN THE broker redelivers the original and the idempotent processing of Requirement 11 SHALL absorb the duplicate.

### Requirement 10: Unreadable Plate Handling

> Revisado em 2026-11-16 (D20).

**User Story:** As a system operator, I want a photo whose plate cannot be read to be recorded as such without any effect on the vehicle, so that the driver leaves with the ticket and nobody has to act.

#### Acceptance Criteria

1. IF the Plate_Normalizer returns an unreadable-plate result for a message, THEN THE Poller SHALL set the Session_Record `plate_status` to `UNREADABLE`, only if it is still `PENDING`, and SHALL NOT write a plate value nor change `status`.
2. IF the Poller sets `plate_status` to `UNREADABLE`, THEN THE Poller SHALL record an `OCR_FAILED` Audit_Entry that includes the `session_id`, `plate_status` and a reason indicating the plate was unreadable.
3. IF the Plate_Normalizer returns an unreadable-plate result, THEN THE Consumer SHALL acknowledge the message on that same delivery, because the OCR is deterministic and a redelivery would produce the same result.
4. IF recording `UNREADABLE` fails due to an RDS error, THEN THE Poller SHALL leave the message available for redelivery.
5. WHEN the Poller classifies a message as a Poison_Message, THE Poller SHALL record a Poison_Message error entry identifying the `session_id` and SHALL set `plate_status` to `UNREADABLE` if it is still `PENDING`, whatever the failure was, so that no session waits for a reading that will not come.

### Requirement 11: Idempotent Message Processing

**User Story:** As a developer, I want redelivered messages handled idempotently, so that duplicate processing does not duplicate side effects.

#### Acceptance Criteria

1. WHEN a message is received whose Session_Record `plate_status` is no longer `PENDING` (`READ`, `UNREADABLE` or `MANUAL`), THE Message_Processor SHALL report the message as terminal (acknowledged) without writing any record, whatever the session `status` is.
2. WHEN a message is received whose Session_Record does not exist (no matching session identifier), THE Message_Processor SHALL report the message as terminal (acknowledged).
3. IF the Message_Processor cannot read the Session_Record because the datastore lookup fails, THEN it SHALL NOT report the message as terminal, SHALL record an error indication identifying the failed lookup, and the message SHALL follow the retry path of Requirement 9.
4. WHEN the same message is delivered several times, THE Worker SHALL record the plate and the `OCR_PROCESSING` Audit_Entry at most once per session, because the plate is written only by `UPDATE ... WHERE plate_status = 'PENDING'`.
5. THE Worker SHALL NOT read the session `status`, nor use a spots counter: a car that has already paid still gets its plate recorded as evidence.

### Requirement 12: Poison Message Handling

**User Story:** As a system operator, I want repeatedly failing messages isolated, so that they do not block the queue indefinitely.

#### Acceptance Criteria

1. WHEN a message is classified as a Poison_Message, THE Message_Processor SHALL record the session identifier and a failure reason describing the classification cause in the DynamoDB audit trail.
2. IF a message fails processing on an Attempt equal to or greater than the maximum of 3, THEN THE Message_Processor SHALL classify it as a Poison_Message and THE Consumer SHALL move it to the Dead_Letter_Queue (Requirement 9.3). A failing message that carries `x-attempt` greater than 3 is classified the same way.
3. THE Worker, not the broker, counts the Attempts (header `x-attempt`, absent or invalid meaning 1) and moves the message; THE OCR_Queue is additionally configured to dead-letter into the Dead_Letter_Queue as a safety net when a message is rejected without requeue.
4. WHEN a message is classified as a Poison_Message, THE Consumer SHALL continue consuming subsequent messages without terminating.
5. IF recording the Poison_Message entry fails, THEN THE Audit_Logger SHALL retry the write up to 3 times and, if all attempts fail, THE Worker SHALL log the error and still move the message to the Dead_Letter_Queue.

### Requirement 13: Downstream Connection Failure Resilience

**User Story:** As a system operator, I want the Worker to survive transient failures of S3, RDS, DynamoDB and RabbitMQ, so that processing resumes when dependencies recover.

#### Acceptance Criteria

1. IF the S3_Connector, Session_Repository or Audit_Logger raises a connection error while processing a message, THEN THE Message_Processor SHALL record the error with an indication identifying the failed dependency and SHALL report the message for retry.
2. IF a connection error occurs while processing a message, THEN THE Consumer SHALL send the message to the Retry_Queue (Requirement 9.2) so that it is redelivered after the 30-second TTL.
3. IF a connection error occurs after the RDS plate transition began, THEN THE Message_Processor SHALL roll back the RDS transition, leaving the record unchanged from its pre-processing value (`plate_status` back to `PENDING`).
4. WHEN a connection error occurs while processing a message, THE Consumer SHALL continue consuming subsequent messages.
5. WHILE a downstream dependency remains unreachable, THE Worker SHALL retry each affected message up to 3 attempts before moving it to the Dead_Letter_Queue; a processing error that reaches the Message_Processor unhandled SHALL be treated as a retry, never as a crash.

### Requirement 14: Environment-Driven Configuration

**User Story:** As a developer, I want all configuration supplied through environment variables, so that no secrets are embedded in code and the Worker runs in multiple environments.

#### Acceptance Criteria

1. WHEN the Worker starts, THE Config_Loader SHALL read the following environment variables: `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `RABBITMQ_URL`, `RABBITMQ_QUEUE_NAME`, `DATABASE_URL`, `S3_ENDPOINT_URL`, `S3_BUCKET_NAME`, `DYNAMODB_ENDPOINT_URL` and `DYNAMODB_TABLE_NAME`.
2. IF any of the required environment variables `AWS_REGION`, `RABBITMQ_URL`, `DATABASE_URL`, `S3_BUCKET_NAME` or `DYNAMODB_TABLE_NAME` is absent or an empty string when the Worker starts, THEN THE Config_Loader SHALL write an error indication naming each missing variable, SHALL prevent consumption from starting, and SHALL terminate the startup with a non-success exit status.
3. WHERE `S3_ENDPOINT_URL` or `DYNAMODB_ENDPOINT_URL` is absent or an empty string, THE Config_Loader SHALL treat it as optional and the corresponding client SHALL use the default AWS endpoint for `AWS_REGION`.
4. IF a supplied value for `RABBITMQ_URL` (scheme other than `amqp`/`amqps`), `DATABASE_URL`, `S3_ENDPOINT_URL` or `DYNAMODB_ENDPOINT_URL` (scheme other than `http`/`https`) does not conform to the expected structure, THEN THE Config_Loader SHALL write an error indication naming the invalid variable, SHALL prevent consumption from starting, and SHALL terminate the startup with a non-success exit status.
5. WHEN the Worker writes log output about the broker, it SHALL describe it as `host:port/vhost` only, excluding the credentials embedded in `RABBITMQ_URL`; the values of `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` and the credentials in `DATABASE_URL` SHALL never be logged.
6. WHERE `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are both absent, THE Config_Loader SHALL proceed with startup and the Worker SHALL resolve AWS credentials through the default credential chain (e.g. the EC2 instance profile); IF only one of them is supplied, THEN THE Config_Loader SHALL report the other as missing. WHERE `AWS_SESSION_TOKEN` is supplied, THE Worker SHALL use it together with the static keys.
7. THE Worker SHALL ignore the variables of the previous design (`SQS_QUEUE_URL`, `AWS_ENDPOINT_URL`, `OCR_ENGINE`, `REDIS_URL`) without failing.

### Requirement 15: Independent Service Endpoints

**User Story:** As a developer, I want each backing service to be reachable at its own endpoint, so that the same code runs against local containers (S3-compatible storage, DynamoDB Local) and against real AWS.

#### Acceptance Criteria

1. WHERE `S3_ENDPOINT_URL` is set to a non-empty value, THE Worker SHALL direct all S3 requests to that endpoint using path-style addressing (`host/bucket/key`), because a local S3-compatible storage does not resolve `bucket.host`.
2. WHERE `DYNAMODB_ENDPOINT_URL` is set to a non-empty value, THE Worker SHALL direct all DynamoDB requests to that endpoint.
3. WHERE either variable is absent or empty, THE Worker SHALL direct that service's requests to the default AWS endpoint for the region named in `AWS_REGION`, independently of the other service.
4. IF either variable is set to a value that is not a syntactically valid URL with an `http` or `https` scheme, THEN THE Worker SHALL abort startup before consuming any message and SHALL emit an error indication identifying the invalid variable.
5. THE Worker SHALL use no Amazon Rekognition or other proprietary vision service: plate reading is local (OpenCV + Tesseract).

### Requirement 16: Graceful Shutdown

**User Story:** As a system operator, I want the Worker to shut down cleanly on a termination signal, so that in-flight work is not corrupted and connections are released.

#### Acceptance Criteria

1. WHEN the Worker receives a SIGTERM or SIGINT termination signal, THE Consumer SHALL stop consuming new messages and SHALL end a reconnection back-off in progress within 1 second.
2. WHILE a message is being processed at the time a termination signal is received, THE Consumer SHALL complete or fail that message (including its retry/DLQ publication and acknowledgement) before the process exits, without interrupting it.
3. WHEN a message is delivered after the stop was requested, THE Consumer SHALL hand it back to the broker (`basic_nack` with requeue) without processing it; with `prefetch_count` 1 there is no batch to release, and the broker redelivers every message that was not acknowledged to another consumer.
4. WHEN the Worker exits, THE Worker SHALL close its RabbitMQ, RDS and AWS client connections and SHALL exit with a success status code (0). A process killed externally mid-message leaves the RDS transaction uncommitted and the message for redelivery.
5. IF closing any RabbitMQ, RDS or AWS client connection fails during exit, THEN THE Worker SHALL record an error indication identifying the failed connection and SHALL continue closing the remaining connections before exiting.

