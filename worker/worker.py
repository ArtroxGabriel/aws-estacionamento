"""Python OCR Worker entrypoint and Poller.

Holds the pure ``Session_Message`` parsing/validation layer, the Poller (SQS
long-polling loop, idempotency branch, transactional side effects, poison
classification, graceful shutdown) and the ``main`` startup wiring.

Shared contract (from the Go API, see design.md "SQS Session_Message"):
the SQS message body is a bare JSON object ``{"session_id", "s3_key"}`` with
no SNS envelope.
"""

from __future__ import annotations

import enum
import json
import logging
import os
import re
import signal
import time
from dataclasses import dataclass
from typing import Any

from config import Config, ConfigError, load_config
from ocr.clean import PlateResult
from ocr.clean import normalize as normalize_plate
from ocr.processor import OcrResult, extract_text
from storage.audit import AuditError, AuditLogger
from storage.clients import dynamodb_client, s3_client, sqs_client
from storage.s3_store import RetrievalError, S3Connector
from storage.session_repo import SessionRepository
from storage.spots import (
    SpotsCounter,
    SpotsError,
    SpotsUnderflowError,
)

logger = logging.getLogger(__name__)

# The message body is bounded by SQS at 256 KB (Req 2.1). ``session_id`` is
# exactly 32 hexadecimal characters (Req 2.5); ``s3_key`` is non-empty and at
# most 1024 characters (Req 2.6).
MAX_BODY_BYTES = 256 * 1024
SESSION_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
MAX_S3_KEY_LEN = 1024

# Must equal the ``maxReceiveCount`` of the redrive policy on
# ``ocr-processamento-fila`` (infra/main.tf). SQS moves the message to the DLQ
# right after its MAX_RECEIVE_COUNT-th receive, so a message that fails on that
# delivery is classified as a Poison_Message and audited then (Req 12.1-12.3).
MAX_RECEIVE_COUNT = 3

# --- Loop / shutdown tuning ------------------------------------------------
#
# SQS long-polling parameters (Req 1.1): each ReceiveMessage waits up to 20 s
# for messages and pulls at most 10 at a time.
RECEIVE_WAIT_SECONDS = 20
MAX_MESSAGES = 10

# On an SQS connection/authorization error the loop backs off before retrying,
# never terminating (Req 1.5). The back-off is capped at 30 s.
MAX_RECEIVE_BACKOFF_SECONDS = 30

# Graceful shutdown bound (Req 16.2, 16.3): after a stop signal, the in-flight
# message must finish within this many seconds or be abandoned (no delete).
SHUTDOWN_TIMEOUT_SECONDS = 30

# The queue visibility timeout the Worker expects while a message is in flight
# (Req 9.4). This is an infra-configured value the Worker documents rather than
# sets; it bounds how long a retained/abandoned message stays invisible before
# redelivery.
VISIBILITY_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class SessionMessage:
    """A parsed and validated SQS ``Session_Message`` payload.

    Both fields are UTF-8 strings extracted verbatim from the JSON body
    (Req 2.2); ``session_id`` is guaranteed to be 32 hex characters and
    ``s3_key`` to be a non-empty string of at most 1024 characters.
    """

    session_id: str
    s3_key: str


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of parsing/validating a raw SQS message body.

    ``ok`` is ``True`` only when the body is a well-formed ``Session_Message``.
    On success, ``message`` holds the parsed payload and ``reason`` is ``None``.
    On failure (poison/invalid), ``message`` is ``None`` and ``reason`` carries
    a specific, human-readable classification cause; the caller retains the
    original, unmodified body (Req 2.3-2.6).
    """

    ok: bool
    message: SessionMessage | None
    reason: str | None


def parse_session_message(body: str) -> ValidationResult:
    """Parse and validate a raw SQS message body as a ``Session_Message``.

    The body is parsed as JSON exactly as delivered, without unwrapping any
    SNS envelope (Req 2.1). On success the ``session_id`` and ``s3_key`` field
    values are extracted as UTF-8 strings (Req 2.2). Every body that is not a
    well-formed ``Session_Message`` is classified as poison/invalid with a
    specific ``reason`` while leaving ``body`` unmodified (Req 2.3-2.6).

    This function never raises for a malformed body; domain outcomes are
    reported through the returned :class:`ValidationResult`.
    """
    # Req 2.3: invalid JSON is a poison message with a malformed-JSON reason.
    try:
        parsed = json.loads(body)
    except ValueError, TypeError:
        return ValidationResult(ok=False, message=None, reason="malformed JSON")

    # A bare JSON object is required; anything else (array, string, number,
    # null, SNS envelope treated as a plain object without the expected fields)
    # cannot carry the Session_Message fields (Req 2.1, 2.4).
    if not isinstance(parsed, dict):
        return ValidationResult(ok=False, message=None, reason="body is not a JSON object")

    # Req 2.2 / 2.4: extract session_id and s3_key as UTF-8 strings, rejecting
    # missing, non-string, or empty values with a field-specific reason.
    session_id = parsed.get("session_id")
    if session_id is None:
        return ValidationResult(ok=False, message=None, reason="missing session_id")
    if not isinstance(session_id, str):
        return ValidationResult(ok=False, message=None, reason="session_id is not a string")
    if session_id == "":
        return ValidationResult(ok=False, message=None, reason="empty session_id")

    s3_key = parsed.get("s3_key")
    if s3_key is None:
        return ValidationResult(ok=False, message=None, reason="missing s3_key")
    if not isinstance(s3_key, str):
        return ValidationResult(ok=False, message=None, reason="s3_key is not a string")
    if s3_key == "":
        return ValidationResult(ok=False, message=None, reason="empty s3_key")

    # Req 2.5: session_id must be exactly 32 hexadecimal characters.
    if not SESSION_ID_RE.fullmatch(session_id):
        return ValidationResult(ok=False, message=None, reason="invalid session_id format")

    # Req 2.6: s3_key must not exceed 1024 characters (emptiness handled above).
    if len(s3_key) > MAX_S3_KEY_LEN:
        return ValidationResult(ok=False, message=None, reason="invalid s3_key format")

    return ValidationResult(
        ok=True,
        message=SessionMessage(session_id=session_id, s3_key=s3_key),
        reason=None,
    )


# --- Poller ---------------------------------------------------------------
#
# The Poller owns SQS orchestration: the long-polling loop, the idempotency
# branch, the ordered side effects, message deletion, poison classification,
# and graceful shutdown.
#
# Session status strings — the shared contract with the Go API. The Worker
# only ever advances ``PROCESSING`` -> ``PARKED``; ``PAID`` is a terminal
# state owned by the API's exit/payment flow.
STATUS_PROCESSING = "PROCESSING"
STATUS_PARKED = "PARKED"
STATUS_PAID = "PAID"

# Statuses that make a message terminal/idempotent: the session already
# advanced past PROCESSING, so no side effects re-run and the message is
# deleted (Req 11.1, 11.2).
_TERMINAL_STATUSES = frozenset({STATUS_PARKED, STATUS_PAID})


class Outcome(enum.Enum):
    """How the Poller should treat a message after ``_handle`` returns.

    The mapping to the actual SQS operation is applied by the loop (task 15.1):
      - ``DELETE`` — success, or a terminal/idempotent state (already
        ``PARKED``/``PAID``, or the session does not exist): ``DeleteMessage``
        (Req 1.4, 9.1, 11.1, 11.2, 11.3).
      - ``RETAIN`` — a transient failure, a downstream connection error, or an
        unreadable plate: leave the message for redelivery, never delete
        (Req 9.2, 10.1, 10.3, 11.4, 13.1, 13.3). This is the fail-safe default.
      - ``POISON`` — the message exceeded the configured receive count or is
        malformed: rely on the SQS redrive policy to move it (task 14.1).
    """

    DELETE = "delete"
    RETAIN = "retain"
    POISON = "poison"


class Poller:
    """Consumes ``Session_Message`` events and advances parking sessions.

    Wires together the pure OCR layer (``ocr/``) and the I/O connectors
    (``storage/``) supplied at construction. All collaborators are injected so
    the orchestration logic can be exercised with in-memory fakes.

    The ``_handle`` method drives one message end-to-end through the fixed
    pipeline from design.md "Processing Pipeline": parse/validate -> session
    lookup -> idempotency branch -> S3 download -> OCR -> normalize -> ordered
    side effects (RDS conditional UPDATE -> Redis DECR -> DynamoDB PutItem)
    -> report ``DELETE`` so the loop performs the SQS ``DeleteMessage`` last.

    The guiding rule is **fail safe toward RETAIN**: any pipeline error,
    lookup failure, unreadable plate, or downstream connection error leaves the
    message in the queue for redelivery and never deletes it.
    """

    def __init__(
        self,
        cfg: Config,
        sqs: Any,
        s3: S3Connector,
        sessions: SessionRepository,
        spots: SpotsCounter,
        audit: AuditLogger,
        ocr: Any = None,
        normalizer: Any = None,
    ) -> None:
        """Store configuration and the injected connectors/pure functions.

        ``sqs`` is the low-level boto3 SQS client (the loop uses it for
        ``ReceiveMessage``/``DeleteMessage``); ``s3``/``sessions``/``spots``/
        ``audit`` are the ``storage/`` connectors. ``ocr`` and ``normalizer``
        default to the pure ``ocr/`` functions (``extract_text`` and
        ``normalize``) but may be overridden with fakes in tests. Matches the
        design's constructor signature
        ``__init__(self, cfg, sqs, s3, sessions, spots, audit, ocr, normalizer)``.
        """

        self._cfg = cfg
        self._sqs = sqs
        self._s3 = s3
        self._sessions = sessions
        self._spots = spots
        self._audit = audit
        # Default to the pure OCR-layer functions; tests may inject fakes.
        self._ocr = ocr if ocr is not None else extract_text
        self._normalizer = normalizer if normalizer is not None else normalize_plate

        # --- Loop / shutdown state ---------------------------------------
        # ``request_stop`` sets this flag from a signal handler; the loop reads
        # it before every receive so new receives stop within 1 s (Req 16.1).
        self._stop = False
        # Wall-clock deadline for the in-flight message once a stop is
        # requested; ``None`` while no shutdown is pending (Req 16.2, 16.3).
        self._shutdown_deadline: float | None = None
        # Set to True if a stop arrives while a message is mid-flight and that
        # message could not finish within the bound — it is abandoned without
        # deletion, which forces a non-zero exit code (Req 16.3, 16.4).
        self._abandoned = False
        # Consecutive receive back-off, doubled on each connection/auth error
        # and capped at MAX_RECEIVE_BACKOFF_SECONDS (Req 1.5).
        self._receive_backoff = 1.0

    def _handle(self, msg: dict[str, Any]) -> Outcome:
        """Process a single SQS message end-to-end and return its outcome.

        ``msg`` is a boto3 SQS message dict; ``Body`` carries the payload and
        ``Attributes.ApproximateReceiveCount`` drives poison classification
        (the ``ReceiptHandle`` is used by the loop to delete). Domain outcomes
        are returned as :class:`Outcome` values; this method never raises for
        an expected failure — connector exceptions are caught and mapped to
        ``RETAIN`` per the design taxonomy.

        Poison classification (Req 10.4, 12.1-12.3): the SQS redrive policy
        moves a message to the DLQ right after its ``MAX_RECEIVE_COUNT``-th
        receive, so that delivery is the Worker's last chance to see it. A
        message that fails on that delivery is classified ``POISON`` and its
        failure reason is audited once. A message seen *beyond* the threshold
        means the redrive is missing or lagging: it was already audited, so it
        is neither reprocessed nor re-audited.
        """

        body = msg.get("Body", "")
        receive_count = self._receive_count(msg)

        if receive_count > MAX_RECEIVE_COUNT:
            logger.warning(
                "message received %d times (max %d); poison already recorded, "
                "leaving it for the SQS redrive policy",
                receive_count,
                MAX_RECEIVE_COUNT,
            )
            return Outcome.POISON

        outcome, reason = self._process(body)

        if outcome is Outcome.RETAIN and receive_count >= MAX_RECEIVE_COUNT:
            parsed = parse_session_message(body)
            session_id = parsed.message.session_id if parsed.message else "unknown"
            logger.warning(
                "poison message (session=%s) after %d deliveries: %s",
                session_id,
                receive_count,
                reason,
            )
            self._log_poison_best_effort(session_id, reason or "processing failed")
            return Outcome.POISON

        return outcome

    def _process(self, body: str) -> tuple[Outcome, str | None]:
        """Drive one message body through the pipeline.

        Returns the outcome plus, for ``RETAIN``, the failure reason that is
        audited if this turns out to be the message's last delivery.
        """

        # 1. Parse + validate the Session_Message (Req 2.x). A malformed body is
        # left in the queue; its poison reason is audited on the last delivery.
        parsed = parse_session_message(body)
        if not parsed.ok or parsed.message is None:
            reason = parsed.reason or "invalid message"
            logger.warning("invalid message: %s", reason)
            return Outcome.RETAIN, reason

        message = parsed.message
        session_id = message.session_id

        # 2. Look up the session by id. A lookup failure (RDS unreachable) must
        # NOT delete the message — leave it for redelivery (Req 11.4, 13.1).
        try:
            row = self._sessions.get(session_id)
        except Exception as exc:  # noqa: BLE001 - map any lookup failure to RETAIN
            logger.error(
                "session lookup failed for %s: dependency=RDS: %s",
                session_id,
                exc,
            )
            return Outcome.RETAIN, "session lookup failed (RDS)"

        # 3. Idempotency branch (Req 11.1, 11.2, 11.3). A missing session or a
        # session already past PROCESSING is terminal: delete without side
        # effects.
        if row is None:
            logger.info("session %s not found; deleting message", session_id)
            return Outcome.DELETE, None
        if row.status in _TERMINAL_STATUSES:
            logger.info(
                "session %s already %s; deleting message (idempotent)",
                session_id,
                row.status,
            )
            return Outcome.DELETE, None
        if row.status != STATUS_PROCESSING:
            # Any other unexpected status is treated conservatively: leave the
            # message for redelivery rather than deleting a state we do not own.
            logger.warning(
                "session %s in unexpected status %s; retaining message",
                session_id,
                row.status,
            )
            return Outcome.RETAIN, f"unexpected session status {row.status}"

        # 4. Download the photo from S3 (Req 3.x). Any retrieval error retains
        # the message (fail safe toward redelivery).
        try:
            image_bytes = self._s3.download(message.s3_key)
        except RetrievalError as exc:
            logger.error(
                "S3 download failed for %s (key=%s): kind=%s dependency=S3",
                session_id,
                message.s3_key,
                exc.kind,
            )
            return Outcome.RETAIN, f"S3 download failed: {exc.kind}"
        except Exception as exc:  # noqa: BLE001 - unexpected S3 failure -> RETAIN
            logger.error(
                "S3 download error for %s (key=%s): dependency=S3: %s",
                session_id,
                message.s3_key,
                exc,
            )
            return Outcome.RETAIN, "S3 download failed"

        # 5. OCR extraction (Req 4.x). Domain failures come back as an
        # OcrResult with ok=False; retain and log the step.
        ocr_result: OcrResult = self._ocr(image_bytes)
        if not ocr_result.ok or ocr_result.raw_text is None:
            logger.warning(
                "OCR failed for %s: error=%s",
                session_id,
                ocr_result.error,
            )
            return Outcome.RETAIN, f"OCR failed: {ocr_result.error}"

        # 6. Normalize the raw text into a canonical plate (Req 5.x). An
        # unreadable plate leaves the session in PROCESSING and does NOT delete
        # the message (Req 10.1, 10.2, 10.3).
        plate_result: PlateResult = self._normalizer(
            ocr_result.raw_text, mercosul=ocr_result.mercosul
        )
        if not plate_result.ok or plate_result.plate is None:
            logger.warning(
                "unreadable plate for %s: reason=%s; leaving status PROCESSING",
                session_id,
                plate_result.reason,
            )
            return Outcome.RETAIN, f"unreadable plate: {plate_result.reason}"

        # 7. Ordered side effects. The plate is readable and the session is
        # PROCESSING, so apply the durable transition and its follow-on effects.
        return self._apply_side_effects(session_id, plate_result.plate)

    def _receive_count(self, msg: dict[str, Any]) -> int:
        """Return the message's SQS ``ApproximateReceiveCount`` (Req 12.2).

        The value lives in the SQS system attribute map under
        ``msg["Attributes"]["ApproximateReceiveCount"]`` (a string). It is only
        present when the ``ReceiveMessage`` call requested that attribute, so a
        missing or unparseable value defaults to ``1`` (first delivery) — the
        conservative choice that never spuriously classifies a message as
        poison on count alone.
        """

        attributes = msg.get("Attributes") or {}
        raw = attributes.get("ApproximateReceiveCount")
        if raw is None:
            return 1
        try:
            count = int(raw)
        except TypeError, ValueError:
            return 1
        # Guard against a nonsensical non-positive value from the attribute.
        return count if count >= 1 else 1

    def _apply_side_effects(self, session_id: str, plate: str) -> tuple[Outcome, str | None]:
        """Apply RDS -> Redis -> DynamoDB atomically; the loop deletes SQS last.

        The RDS conditional ``UPDATE`` is the idempotency gate (Req 6.1, 6.6,
        11.5), and its transaction stays open while the follow-on effects run:

          1. ``UPDATE ... WHERE status='PROCESSING'`` inside a transaction. Zero
             rows updated means another delivery already advanced the row, so
             nothing else runs and the message is deleted.
          2. Redis ``DECR`` — only on the transition, so exactly once per
             session (Req 7.1, 7.4). Underflow is clamped by the connector and
             does not abort the transition (Req 7.5).
          3. DynamoDB ``PutItem`` with ``action=OCR_PROCESSING`` (Req 8.1).
          4. ``COMMIT``.

        If any step fails, the transaction rolls back so the row stays
        ``PROCESSING`` and the message is retained (Req 9.2, 13.3). A DECR that
        already happened is compensated with an ``INCR``, so the redelivery
        re-runs the whole pipeline from a clean state instead of finding a
        ``PARKED`` row whose side effects were never applied. The only effect
        that cannot be undone is an audit entry written right before a failed
        COMMIT; that leaves an extra ``OCR_PROCESSING`` entry, never a missing
        one.
        """

        step = "RDS"
        decremented = False
        try:
            with self._sessions.parking_transition(session_id, plate) as transitioned:
                if not transitioned:
                    logger.info(
                        "session %s no longer PROCESSING at update time; "
                        "skipping side effects, deleting message",
                        session_id,
                    )
                    return Outcome.DELETE, None

                step = "Redis"
                try:
                    self._spots.decrement()
                    decremented = True
                except SpotsUnderflowError as exc:
                    # Counter clamped at 0 (net effect zero): nothing to
                    # compensate, and the transition proceeds.
                    logger.error("spots counter underflow for %s: %s", session_id, exc)

                step = "DynamoDB"
                self._audit.log_ocr(session_id, plate)

                step = "RDS commit"
        except Exception as exc:  # noqa: BLE001 - any failure rolls back -> RETAIN
            logger.error(
                "side effects failed for %s at step=%s; RDS rolled back: %s",
                session_id,
                step,
                exc,
            )
            if decremented:
                self._compensate_decrement(session_id)
            return Outcome.RETAIN, f"{step} failed"

        logger.info("session %s parked with plate %s", session_id, plate)
        return Outcome.DELETE, None

    def _compensate_decrement(self, session_id: str) -> None:
        """Undo a DECR whose RDS transaction rolled back."""

        try:
            self._spots.increment()
        except Exception as exc:  # noqa: BLE001 - nothing else can be done here
            logger.error(
                "failed to compensate spots decrement for %s; counter is now one "
                "below the committed state until rehydrated: dependency=Redis: %s",
                session_id,
                exc,
            )

    def _log_poison_best_effort(self, session_id: str, reason: str) -> None:
        """Record a poison classification reason without blocking (Req 12.1).

        ``AuditLogger`` already retries the write up to 3 times; a final failure
        is logged and swallowed so the Poller keeps consuming (Req 12.5).
        """

        try:
            self._audit.log_poison(session_id, reason)
        except Exception as exc:  # noqa: BLE001 - poison audit is best-effort
            logger.error(
                "failed to record poison reason for %s: %s",
                session_id,
                exc,
            )

    # --- Loop, receive, signals, and shutdown ----------------------------

    def run(self) -> int:
        """Run the SQS long-polling loop until a stop is requested (Req 1.x, 16.x).

        The loop repeatedly calls :meth:`_receive` and processes each returned
        message sequentially through :meth:`_handle`, mapping the resulting
        :class:`Outcome` to an SQS action: ``DELETE`` performs the
        ``DeleteMessage``; ``RETAIN``/``POISON`` leave the message in the queue
        for redelivery or SQS redrive respectively (Req 1.3, 1.4, 9.2, 12.3).

        Resilience is the priority: any per-message exception is caught, logged
        with the failed step, and the loop continues to the next message within
        1 s (Req 12.4, 13.4). An empty receive simply re-polls (Req 1.2), and a
        receive connection/authorization error is absorbed inside
        :meth:`_receive` (Req 1.5), so this loop never terminates on transient
        failure — only on a stop signal.

        On stop the loop stops requesting new messages (Req 16.1), finishes the
        current message within the shutdown bound (Req 16.2), then closes all
        connections and returns the exit code: ``0`` iff no in-flight message
        was abandoned, non-zero otherwise (Req 16.4).
        """

        try:
            while not self._stop:
                messages = self._receive()
                for msg in messages:
                    # Honor a stop that arrived mid-batch: stop starting new
                    # messages within the bound so shutdown stays prompt.
                    if self._stop and self._shutdown_expired():
                        logger.warning(
                            "shutdown bound exceeded; abandoning remaining "
                            "%d message(s) in batch without deletion",
                            len(messages),
                        )
                        self._abandoned = True
                        break
                    self._process_one(msg)
        finally:
            self.close()

        return 0 if not self._abandoned else 1

    def _process_one(self, msg: dict[str, Any]) -> None:
        """Handle one message and apply its SQS action, never raising.

        Any exception from :meth:`_handle` or the delete is caught and logged so
        the loop continues within 1 s (Req 12.4, 13.4). A message is deleted
        only on :attr:`Outcome.DELETE`; ``RETAIN`` and ``POISON`` leave it in
        the queue (Req 9.2, 12.3).
        """

        try:
            outcome = self._handle(msg)
        except Exception as exc:  # noqa: BLE001 - loop must survive any message
            logger.error(
                "unhandled error processing message; leaving in queue: "
                "step=handle dependency=worker: %s",
                exc,
            )
            return

        if outcome is Outcome.DELETE:
            self._delete(msg)
        # RETAIN / POISON: leave the message in the queue (redelivery / redrive).

    def _receive(self) -> list[dict[str, Any]]:
        """Long-poll the SQS queue and return the received messages (Req 1.1).

        Requests up to :data:`MAX_MESSAGES` messages with a
        :data:`RECEIVE_WAIT_SECONDS`-second wait and asks for the
        ``ApproximateReceiveCount`` system attribute so :meth:`_receive_count`
        can classify poison messages. An empty receive returns an empty list and
        the caller re-polls immediately (Req 1.2).

        On an SQS connection or authorization error this backs off up to
        :data:`MAX_RECEIVE_BACKOFF_SECONDS` and returns an empty list rather
        than raising, so the loop keeps running without terminating (Req 1.5).
        """

        # A stop requested between receives: do not start a new long poll.
        if self._stop:
            return []

        try:
            response = self._sqs.receive_message(
                QueueUrl=self._cfg.sqs_queue_url,
                WaitTimeSeconds=RECEIVE_WAIT_SECONDS,
                MaxNumberOfMessages=MAX_MESSAGES,
                AttributeNames=["ApproximateReceiveCount"],
            )
        except Exception as exc:  # noqa: BLE001 - connection/auth error -> back off
            backoff = self._receive_backoff
            logger.error(
                "SQS receive failed: dependency=SQS; backing off %.1fs: %s",
                backoff,
                exc,
            )
            self._sleep(backoff)
            # Exponential back-off, capped so the loop retries within 30 s.
            self._receive_backoff = min(backoff * 2, float(MAX_RECEIVE_BACKOFF_SECONDS))
            return []

        # Successful receive: reset the back-off window.
        self._receive_backoff = 1.0
        messages = response.get("Messages") or []
        return messages

    def _delete(self, msg: dict[str, Any]) -> None:
        """Delete a fully-processed message from the SQS queue (Req 1.4, 9.1).

        Uses the message ``ReceiptHandle`` against the configured queue URL. A
        delete failure is logged but not raised: the message will simply be
        redelivered and short-circuit at the idempotency branch (already
        ``PARKED``), so the loop continues (Req 13.4).
        """

        receipt_handle = msg.get("ReceiptHandle")
        if not receipt_handle:
            logger.error("cannot delete message: missing ReceiptHandle")
            return
        try:
            self._sqs.delete_message(
                QueueUrl=self._cfg.sqs_queue_url,
                ReceiptHandle=receipt_handle,
            )
        except Exception as exc:  # noqa: BLE001 - delete failure -> redelivery
            logger.error(
                "SQS delete failed; message will be redelivered: dependency=SQS: %s",
                exc,
            )

    def request_stop(self, signum: Any = None, frame: Any = None) -> None:
        """Signal handler for SIGTERM/SIGINT that requests a graceful stop.

        Sets the stop flag so the loop stops requesting new messages within 1 s
        (Req 16.1) and arms the shutdown deadline that bounds finishing the
        in-flight message (Req 16.2). Safe to call more than once; the first
        call fixes the deadline.
        """

        if not self._stop:
            logger.info("stop requested (signal=%s); draining", signum)
        self._stop = True
        if self._shutdown_deadline is None:
            self._shutdown_deadline = self._now() + SHUTDOWN_TIMEOUT_SECONDS

    def _shutdown_expired(self) -> bool:
        """Return True once the graceful-shutdown bound has elapsed (Req 16.3)."""

        if self._shutdown_deadline is None:
            return False
        return self._now() >= self._shutdown_deadline

    def close(self) -> None:
        """Close RDS/Redis/AWS connections, surviving individual failures.

        Each connector is closed independently; a failure closing one is logged
        with the failed dependency and the routine proceeds to the next, so no
        single close error prevents releasing the others (Req 16.5). Connectors
        without a ``close`` are skipped. This runs exactly once at loop exit.
        """

        # (connector, dependency-label) pairs; only those exposing close() are
        # invoked. Redis (spots) is explicitly required to be closed (Req 16.5).
        for connector, dependency in (
            (self._spots, "Redis"),
            (self._sessions, "RDS"),
            (self._sqs, "SQS"),
            (self._s3, "S3"),
            (self._audit, "DynamoDB"),
        ):
            close = getattr(connector, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception as exc:  # noqa: BLE001 - keep closing the rest
                logger.error(
                    "failed to close %s connection; continuing: %s",
                    dependency,
                    exc,
                )

    def _now(self) -> float:
        """Monotonic clock for shutdown timing; overridable in tests."""

        return time.monotonic()

    def _sleep(self, seconds: float) -> None:
        """Sleep for the receive back-off; overridable in tests."""

        time.sleep(seconds)


# --- Entrypoint -----------------------------------------------------------
#
# ``main`` is the Config_Loader -> client factory -> connector -> Poller wiring
# from design.md ("Startup / entrypoint"). It fails fast with a non-zero exit
# code before the polling loop begins when configuration is missing/invalid
# (Req 1.7, 14.2) or a connector cannot be constructed (Req 6.2, 7.3, 8.6), and
# otherwise returns the Poller's own exit code (Req 16.4).


def main(argv: list[str] | None = None) -> int:
    """Wire configuration, clients, connectors and the Poller, then run it.

    Startup order (fail fast before the loop, Req 1.7):
      1. Configure basic logging.
      2. Load + validate configuration from the environment; a
         :class:`ConfigError` lists every missing/invalid variable and returns a
         non-zero exit code without starting the loop (Req 14.2, 16.4).
      3. Build the S3/SQS/DynamoDB boto3 clients through the shared factory so
         every client resolves the same endpoint (Req 15.1, 15.5).
      4. Construct the connectors. ``SpotsCounter`` and ``AuditLogger`` fail
         construction on an absent target (Req 7.3, 8.6); any construction error
         is caught and returns a non-zero exit code before the loop.
      5. Construct the Poller, injecting cfg + the SQS client + connectors +
         the pure OCR/normalizer functions.
      6. Install SIGTERM/SIGINT handlers pointing at ``poller.request_stop`` for
         graceful shutdown (Req 16.1).
      7. Run the loop and return its exit code (0 iff no in-flight message was
         abandoned, non-zero otherwise; Req 16.4).

    Returns:
        The process exit code: non-zero on any startup failure, otherwise the
        value returned by :meth:`Poller.run`.
    """

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Step 2: load + validate configuration (fail fast, Req 1.7, 14.2).
    try:
        cfg = load_config(os.environ)
    except ConfigError as exc:
        # ConfigError already aggregates every missing/invalid variable by
        # *name only* (never value), so logging it cannot leak a secret; the
        # redact() marker is available for any place that must echo a value.
        logger.error("configuration error; aborting before the loop: %s", exc)
        return 2

    # Step 3: build the AWS clients through the shared factory (Req 15.1, 15.5).
    # Step 4: construct the connectors. SpotsCounter/AuditLogger raise on an
    # absent target (Req 7.3, 8.6); catch any construction failure and fail fast
    # rather than entering the loop half-wired (Req 1.7).
    try:
        s3 = s3_client(cfg)
        sqs = sqs_client(cfg)
        dynamodb = dynamodb_client(cfg)

        s3_connector = S3Connector(s3, cfg.s3_bucket_name)
        sessions = SessionRepository(cfg.database_url)
        spots = SpotsCounter(cfg.redis_url)
        audit = AuditLogger(dynamodb, cfg.dynamodb_table_name)
    except (SpotsError, AuditError) as exc:
        logger.error("connector initialization failed; aborting: %s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001 - any startup failure must fail fast
        logger.error("startup failed while building clients/connectors: %s", exc)
        return 2

    # Step 5: construct the Poller with all collaborators injected. The pure
    # OCR-layer functions (extract_text/normalize_plate) are passed explicitly
    # so no component from tasks 2-15 is left orphaned.
    poller = Poller(
        cfg=cfg,
        sqs=sqs,
        s3=s3_connector,
        sessions=sessions,
        spots=spots,
        audit=audit,
        ocr=extract_text,
        normalizer=normalize_plate,
    )

    # Step 6: install graceful-shutdown signal handlers (Req 16.1).
    signal.signal(signal.SIGTERM, poller.request_stop)
    signal.signal(signal.SIGINT, poller.request_stop)

    # Step 7: run the loop and return its exit code (Req 16.4).
    logger.info(
        "worker started; polling %s (endpoint=%s)",
        cfg.sqs_queue_url,
        cfg.aws_endpoint_url or "default AWS",
    )
    return poller.run()


if __name__ == "__main__":
    raise SystemExit(main())
