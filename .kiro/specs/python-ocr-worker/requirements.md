# Requirements Document

## Introduction

The Python OCR Worker is the asynchronous background component of the AWS parking-lot management system (Trabalho Prático de Cloud da UFC). It continuously consumes vehicle-entry events from an Amazon SQS queue, downloads the corresponding vehicle photo from Amazon S3, extracts the license plate through image pre-processing and Tesseract OCR, and then transitions the parking session forward. On successful extraction the Worker updates the relational session record in Amazon RDS (PostgreSQL), atomically decrements the available-spots counter in Amazon ElastiCache (Redis), writes an immutable audit entry to Amazon DynamoDB, and finally removes the processed message from SQS.

The Worker must operate identically against a local emulated environment (Floci at `http://localhost:4566`) and against real AWS Academy infrastructure, driven exclusively by environment variables. It must be resilient to OCR failures, unreadable plates, message redelivery, poison messages, downstream connection failures, and process shutdown signals, while preserving the exactness of the shared data contracts defined by the API component.

## Glossary

- **Worker**: The Python 3.14 asynchronous process consuming the SQS queue and orchestrating the OCR pipeline.
- **Poller**: The component of the Worker responsible for the continuous SQS long-polling loop (`worker.py`).
- **OCR_Processor**: The component that pre-processes images and extracts raw text via Tesseract (`ocr/processor.py`).
- **Plate_Normalizer**: The component that normalizes raw OCR text into a valid plate format (`ocr/clean.py`).
- **S3_Connector**: The storage component that downloads vehicle photos from Amazon S3 (`storage/`).
- **Session_Repository**: The storage component that reads and updates session records in Amazon RDS (PostgreSQL) (`storage/`).
- **Spots_Counter**: The storage component that mutates the available-spots counter in Amazon ElastiCache (Redis) (`storage/`).
- **Audit_Logger**: The storage component that writes immutable audit entries to Amazon DynamoDB (`storage/`).
- **Config_Loader**: The component that loads and validates environment-driven configuration at startup.
- **SQS_Queue**: The Amazon SQS queue named `ocr-processamento-fila`.
- **Session_Message**: The JSON payload carried in the SQS message body, of shape `{"session_id": "<hex_32_chars>", "s3_key": "photos/{session_id}_{filename}"}`.
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

### Requirement 1: Continuous SQS Long-Polling Consumption

**User Story:** As a system operator, I want the Worker to continuously consume entry events from the SQS queue, so that vehicle photos are processed automatically without manual intervention.

#### Acceptance Criteria

1. WHILE the Worker is running, THE Poller SHALL request messages from the SQS_Queue using long polling with a `WaitTimeSeconds` value of 20 seconds and a maximum of 10 messages per receive request.
2. WHEN a receive request returns zero messages, THE Poller SHALL issue a subsequent receive request against the SQS_Queue within 1 second.
3. WHEN a receive request returns one or more messages, THE Poller SHALL process each returned message through the OCR pipeline.
4. WHEN a message has been processed successfully through the OCR pipeline, THE Poller SHALL delete that message from the SQS_Queue.
5. IF a receive request fails due to an SQS_Queue connection or authorization error, THEN THE Poller SHALL retry the receive request after a delay of no more than 30 seconds and SHALL continue running without terminating.
6. THE Poller SHALL read the SQS_Queue URL from the `SQS_QUEUE_URL` environment variable.
7. IF the `SQS_QUEUE_URL` environment variable is absent or empty at startup, THEN THE Poller SHALL terminate startup and produce an error indication identifying the missing configuration.

### Requirement 2: Message Parsing and Validation

**User Story:** As a developer, I want the Worker to parse and validate the incoming message payload, so that only well-formed events enter the OCR pipeline.

#### Acceptance Criteria

1. WHEN a message is received, THE Poller SHALL parse the SQS message body, whose size SHALL NOT exceed 256 KB, as a Session_Message JSON object without unwrapping any SNS envelope.
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

### Requirement 6: Session Update in RDS

**User Story:** As a system operator, I want the Worker to record the identified plate and advance the session status, so that the vehicle is registered as parked.

#### Acceptance Criteria

1. WHEN a plate is successfully normalized, THE Session_Repository SHALL update the Session_Record identified by `session_id`, setting `license_plate` to the normalized plate and `status` to `PARKED`.
2. THE Session_Repository SHALL read the RDS connection string from the `DATABASE_URL` environment variable.
3. IF no Session_Record exists with the given `session_id`, THEN THE Session_Repository SHALL return a not-found error to the Poller and SHALL NOT create a new Session_Record.
4. WHEN the Session_Repository updates a Session_Record, THE Session_Repository SHALL set `license_plate` to a non-empty value between 1 and 16 characters and `status` to a value between 1 and 20 characters.
5. IF the update operation fails due to an RDS connection or query error, THEN THE Session_Repository SHALL return an error to the Poller, SHALL leave the existing Session_Record `license_plate` and `status` values unchanged, and SHALL NOT advance the status to `PARKED`.
6. IF the existing Session_Record identified by `session_id` has a `status` other than `PROCESSING`, THEN THE Session_Repository SHALL return an invalid-state error to the Poller and SHALL leave the Session_Record unchanged.

### Requirement 7: Atomic Available-Spots Decrement in Redis

**User Story:** As a system operator, I want the Worker to decrement the available-spots counter when a vehicle parks, so that spot availability stays accurate.

#### Acceptance Criteria

1. WHEN a Session_Record is updated to `PARKED` for a message not previously processed, THE Spots_Counter SHALL atomically decrement the Spots_Key value by one within 500 milliseconds of the status update.
2. THE Spots_Counter SHALL read the Redis connection target from the `REDIS_URL` environment variable.
3. IF the `REDIS_URL` environment variable is absent or empty at startup, THEN THE Spots_Counter SHALL NOT begin processing messages and SHALL emit a startup error indicating the missing connection target.
4. IF the same message is redelivered after its Session_Record already has status `PARKED`, THEN THE Spots_Counter SHALL NOT decrement the Spots_Key value again.
5. IF the atomic decrement would reduce the Spots_Key value below zero, THEN THE Spots_Counter SHALL leave the Spots_Key value at zero and SHALL emit an error indicating a counter underflow condition.
6. IF the Redis connection is unavailable when the decrement is attempted, THEN THE Spots_Counter SHALL retry the decrement operation up to 3 times with the message remaining unacknowledged, and SHALL emit an error indicating the decrement failed after retries when all attempts are exhausted.
7. IF the Spots_Key does not exist when the decrement (or its compensating increment) is attempted, THEN THE Spots_Counter SHALL leave it absent, atomically on the Redis server, so that the API rebuilds the count from RDS instead of reading a counter the Worker created at zero.

### Requirement 8: Immutable Audit Logging in DynamoDB

**User Story:** As a compliance reviewer, I want every OCR processing event recorded in an immutable audit log, so that all state changes are traceable.

#### Acceptance Criteria

1. WHEN a Session_Record is updated to `PARKED`, THE Audit_Logger SHALL write exactly one Audit_Entry to the Audit_Table with `action` equal to `OCR_PROCESSING` within 5 seconds of the update completing.
2. WHEN the Audit_Logger writes an Audit_Entry, THE Audit_Logger SHALL set the `id` field to the value `<session_id>#<timestamp_nano>`, where `timestamp_nano` is the Unix epoch time in nanoseconds at write.
3. WHEN the Audit_Logger writes an Audit_Entry, THE Audit_Logger SHALL include the identified license plate and the `session_id` in the Audit_Entry.
4. IF an Audit_Entry with an `id` equal to an already-persisted Audit_Entry `id` would be written, THEN THE Audit_Logger SHALL reject the write and preserve the existing Audit_Entry unchanged.
5. IF the write of an Audit_Entry to the Audit_Table fails, THEN THE Audit_Logger SHALL retry the write up to a maximum of 3 attempts and, if all attempts fail, record an error indication identifying the affected `session_id` without altering the Session_Record.
6. THE Audit_Logger SHALL read the DynamoDB table name from the `DYNAMODB_TABLE_NAME` environment variable, and IF the `DYNAMODB_TABLE_NAME` environment variable is unset or empty, THEN THE Audit_Logger SHALL fail initialization with an error indication that the table name is missing.

### Requirement 9: Message Deletion After Successful Processing

**User Story:** As a system operator, I want messages removed from the queue only after full successful processing, so that failures are safely retried.

#### Acceptance Criteria

1. WHEN the RDS update, the Redis decrement, and the DynamoDB audit write all complete successfully for a message, THE Poller SHALL delete that message from the SQS_Queue within 5 seconds of the final step completing.
2. IF any step in the OCR pipeline returns an error for a message, THEN THE Poller SHALL leave that message in the SQS_Queue, preserve the message body unchanged, and log an error entry indicating which processing step failed.
3. IF a message fails processing on the delivery whose receive count equals the configured maximum receive count of 3, THEN THE Poller SHALL record an audit entry indicating the message exhausted retries, and the SQS redrive policy SHALL route that message to the dead-letter queue.
4. WHILE a message is being processed, THE Poller SHALL retain the message with a visibility timeout of 300 seconds so that no other consumer receives it before processing completes or fails.

### Requirement 10: Unreadable Plate Handling

**User Story:** As a system operator, I want the Worker to handle photos whose plate cannot be read, so that such events are surfaced without corrupting session data.

#### Acceptance Criteria

1. IF the Plate_Normalizer returns an unreadable-plate result for a message, THEN THE Poller SHALL leave the corresponding Session_Record status unchanged as `PROCESSING` and SHALL NOT write a plate value to the Session_Record.
2. IF the Plate_Normalizer returns an unreadable-plate result for a message, THEN THE Poller SHALL record an unreadable-plate error entry that includes the `session_id` and a reason indicating the plate was unreadable, without modifying any other Session_Record field.
3. IF the Plate_Normalizer returns an unreadable-plate result for a message, THEN THE Poller SHALL leave the SQS message available for redelivery rather than deleting it.
4. WHEN a message reaches the configured maximum receive count of 3 without producing a readable plate, THE Poller SHALL classify the message as a Poison_Message and SHALL remove it from the active processing flow.
5. WHEN the Poller classifies a message as a Poison_Message, THE Poller SHALL record a Poison_Message error entry identifying the `session_id` while leaving the corresponding Session_Record status as `PROCESSING`.

### Requirement 11: Idempotent Message Processing

**User Story:** As a developer, I want redelivered messages handled idempotently, so that duplicate processing does not double-count spots or duplicate side effects.

#### Acceptance Criteria

1. WHEN a message is received whose Session_Record already has status `PARKED`, THE Poller SHALL delete the message from the SQS_Queue within 5 seconds, SHALL NOT decrement the Spots_Key value, and SHALL NOT write any additional side-effect records.
2. WHEN a message is received whose Session_Record already has status `PAID`, THE Poller SHALL delete the message from the SQS_Queue within 5 seconds, SHALL NOT decrement the Spots_Key value, and SHALL NOT write any additional side-effect records.
3. WHEN a message is received whose Session_Record does not exist (no matching session identifier), THE Poller SHALL delete the message from the SQS_Queue within 5 seconds and SHALL NOT decrement the Spots_Key value.
4. IF the Poller cannot determine the Session_Record status because the datastore lookup fails, THEN THE Poller SHALL NOT delete the message from the SQS_Queue, SHALL NOT decrement the Spots_Key value, SHALL leave the message available for redelivery after the SQS visibility timeout expires, and SHALL record an error indication identifying the failed lookup.
5. WHEN the Poller processes a message whose Session_Record has status `PROCESSING`, THE Poller SHALL decrement the Spots_Key value exactly once per session identifier regardless of the number of times that message is redelivered.

### Requirement 12: Poison Message Handling

**User Story:** As a system operator, I want repeatedly failing messages isolated, so that they do not block the queue indefinitely.

#### Acceptance Criteria

1. WHEN a message is classified as a Poison_Message, THE Poller SHALL record the message identifier and a failure reason describing the classification cause in the DynamoDB audit trail.
2. IF a message fails processing on a delivery whose receive count is equal to or greater than the configured maximum receive count of 3, THEN THE Poller SHALL classify the message as a Poison_Message; a message whose receive count exceeds the maximum SHALL NOT be reprocessed nor audited again.
3. WHERE a dead-letter queue is configured for the SQS_Queue, THE Poller SHALL rely on the SQS redrive policy to move a Poison_Message to the dead-letter queue after the message reaches the configured maximum receive count of 3.
4. WHEN a message is classified as a Poison_Message, THE Poller SHALL continue consuming subsequent messages from the SQS_Queue within 1 second without terminating the polling loop.
5. IF recording the Poison_Message identifier and failure reason fails, THEN THE Poller SHALL retry the record operation up to 3 times and, if all attempts fail, continue consuming subsequent messages without blocking the SQS_Queue.

### Requirement 13: Downstream Connection Failure Resilience

**User Story:** As a system operator, I want the Worker to survive transient failures of S3, RDS, Redis, and DynamoDB, so that processing resumes when dependencies recover.

#### Acceptance Criteria

1. IF the S3_Connector, Session_Repository, Spots_Counter, or Audit_Logger raises a connection error while processing a message, THEN THE Poller SHALL record the error with an indication identifying the failed dependency and SHALL NOT delete the message from the SQS_Queue.
2. IF a connection error occurs while processing a message, THEN THE Poller SHALL leave the message in the SQS_Queue so that it becomes available for redelivery after the SQS visibility timeout of 300 seconds elapses.
3. IF a connection error occurs while processing a message, THEN THE Poller SHALL roll back the RDS transition and compensate any Redis decrement already applied, leaving the affected records unchanged from their pre-processing values.
4. WHEN a connection error occurs while processing a message, THE Poller SHALL continue the polling loop for subsequent messages within 1 second of recording the error.
5. WHILE a downstream dependency remains unreachable, THE Poller SHALL retry each affected message up to 3 delivery attempts before the SQS_Queue routes the message to the dead-letter queue.

### Requirement 14: Environment-Driven Configuration

**User Story:** As a developer, I want all configuration supplied through environment variables, so that no secrets are embedded in code and the Worker runs in multiple environments.

#### Acceptance Criteria

1. WHEN the Worker starts, THE Config_Loader SHALL read the following environment variables: `AWS_ENDPOINT_URL`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `SQS_QUEUE_URL`, `DATABASE_URL`, `REDIS_URL`, `S3_BUCKET_NAME`, and `DYNAMODB_TABLE_NAME`.
2. IF any of the required environment variables `AWS_REGION`, `SQS_QUEUE_URL`, `DATABASE_URL`, `REDIS_URL`, `S3_BUCKET_NAME`, or `DYNAMODB_TABLE_NAME` is absent or is an empty string when the Worker starts, THEN THE Config_Loader SHALL write an error indication naming each missing variable, SHALL prevent the polling loop from starting, and SHALL terminate the startup with a non-success exit status.
3. WHERE `AWS_ENDPOINT_URL` is absent or an empty string, THE Config_Loader SHALL treat it as optional and proceed with startup using the default AWS service endpoints without reporting a missing-variable error.
4. IF a supplied value for `SQS_QUEUE_URL`, `DATABASE_URL`, `REDIS_URL`, or `AWS_ENDPOINT_URL` does not conform to the expected URL or connection-string structure for that variable, THEN THE Config_Loader SHALL write an error indication naming the invalid variable, SHALL prevent the polling loop from starting, and SHALL terminate the startup with a non-success exit status.
5. WHEN the Config_Loader writes any log output, THE Config_Loader SHALL exclude the values of `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, and any credential embedded in `DATABASE_URL` or `REDIS_URL`, substituting a fixed redaction marker in place of each such value.
6. WHERE `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are both absent, THE Config_Loader SHALL proceed with startup and the Worker SHALL resolve AWS credentials through the default credential chain (e.g. the EC2 instance profile); IF only one of them is supplied, THEN THE Config_Loader SHALL report the other as missing. WHERE `AWS_SESSION_TOKEN` is supplied, THE Worker SHALL use it together with the static keys.

### Requirement 15: Dual-Mode AWS Endpoint Support

**User Story:** As a developer, I want the Worker to target either the local emulator or real AWS, so that the same code runs in development and in AWS Academy.

#### Acceptance Criteria

1. WHERE the `AWS_ENDPOINT_URL` environment variable is set to a non-empty value, THE Worker SHALL direct all AWS service requests (S3, SQS, DynamoDB) to that endpoint URL.
2. WHERE the `AWS_ENDPOINT_URL` environment variable is absent or set to an empty string, THE Worker SHALL direct all AWS service requests (S3, SQS, DynamoDB) to the default AWS endpoints for the region named in `AWS_REGION`.
3. IF the `AWS_ENDPOINT_URL` environment variable is set to a value that is not a syntactically valid URL with an `http` or `https` scheme, THEN THE Worker SHALL abort startup before consuming any SQS message and SHALL emit an error indication identifying the invalid `AWS_ENDPOINT_URL` value.
4. IF the `AWS_ENDPOINT_URL` environment variable is absent or empty AND the `AWS_REGION` environment variable is absent or empty, THEN THE Worker SHALL abort startup before consuming any SQS message and SHALL emit an error indication identifying the missing region configuration.
5. WHEN the Worker resolves its AWS endpoint configuration at startup, THE Worker SHALL apply the identical endpoint resolution to every AWS service client (S3, SQS, DynamoDB) so that no service client targets a different endpoint than the others.

### Requirement 16: Graceful Shutdown

**User Story:** As a system operator, I want the Worker to shut down cleanly on a termination signal, so that in-flight work is not corrupted and connections are released.

#### Acceptance Criteria

1. WHEN the Worker receives a SIGTERM or SIGINT termination signal, THE Poller SHALL stop requesting new messages, SHALL end a receive back-off in progress within 1 second, and SHALL let an SQS long poll in progress run to its end (at most 20 seconds) and release every message it returns with a visibility timeout of 0, because a long poll abandoned client-side can still take a message on the SQS side and hide it for the whole visibility timeout.
2. WHILE a message is being processed at the time a termination signal is received, THE Poller SHALL complete or fail that message before the process exits, without interrupting it.
3. WHEN a termination signal is received while a batch still has messages not yet started, THE Poller SHALL release those messages back to the SQS_Queue with a visibility timeout of 0 so another consumer receives them immediately.
4. WHEN the Worker exits, THE Worker SHALL close its RDS, Redis, and AWS client connections and SHALL exit with a success status code (0). A process killed externally mid-message leaves the RDS transaction uncommitted and the message for redelivery.
5. IF closing any RDS, Redis, or AWS client connection fails during exit, THEN THE Worker SHALL record an error indication identifying the failed connection and SHALL continue closing the remaining connections before exiting.
