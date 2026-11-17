"""Message processing: the business rules of the OCR Worker, with no queue in sight.

The :class:`MessageProcessor` takes the raw body of one ``Session_Message`` and
the number of the delivery attempt, drives it through the pipeline (parse ->
session lookup -> idempotency branch -> S3 download -> OCR -> normalize -> plate
recording) and reports what the transport must do with the message
(:class:`Outcome`). Everything about RabbitMQ (ack, retry queue, DLQ, shutdown)
lives in ``consumer.py``.

The Worker only *enriches* a session: the car is already in, holding its spot,
and its ticket is valid from the moment the API created the session. All the
Worker does is read the plate from the entry photo and record it
(``plate_status`` ``PENDING`` -> ``READ`` | ``UNREADABLE``). It never changes
the session ``status`` nor the spots counter.

The Worker's whole state machine is ``plate_status``. Anything other than
``PENDING`` (``READ``/``UNREADABLE`` already recorded, ``MANUAL`` typed by the
cashier) makes the message terminal/idempotent. The session ``status``
(``PARKED``/``PAID``) belongs to the API and is never read as a gate here: a car
that already paid still gets its plate recorded as evidence.
"""

from __future__ import annotations

import enum
import logging
from typing import Any, NamedTuple

from ocr.clean import PlateResult
from ocr.clean import normalize as normalize_plate
from ocr.processor import OcrResult, extract_text
from parser import parse_session_message
from storage.audit import AuditLogger
from storage.s3_store import RetrievalError, S3Connector
from storage.session_repo import PLATE_PENDING, SessionRepository

logger = logging.getLogger(__name__)

__all__ = ["MAX_ATTEMPTS", "MessageProcessor", "Outcome", "Result"]

# A message that fails on this delivery attempt is given up on: it is audited as
# a poison message and the consumer moves it to the dead-letter queue.
MAX_ATTEMPTS = 3


class Outcome(enum.Enum):
    """What the transport must do with a message after it was processed.

    - ``DELETE``: the plate was recorded, the photo was recorded as unreadable,
      or the message is terminal/idempotent (``plate_status`` is no longer
      ``PENDING``, or the session does not exist). Acknowledge it.
    - ``RETAIN``: a transient failure or a downstream connection error. Try
      again later (fail-safe default).
    - ``POISON``: the message failed on its last attempt (or is malformed). Send
      it to the dead-letter queue.
    """

    DELETE = "delete"
    RETAIN = "retain"
    POISON = "poison"


class Result(NamedTuple):
    """Outcome of one processing attempt plus the failure reason, when any."""

    outcome: Outcome
    reason: str | None = None


class MessageProcessor:
    """Records the plate of each photo announced by a ``Session_Message``.

    All collaborators are injected so the orchestration logic can be exercised
    with in-memory fakes. ``ocr`` and ``normalizer`` default to the pure ``ocr/``
    functions (``extract_text`` and ``normalize``).

    The guiding rule is **fail safe toward RETAIN**: any pipeline error, lookup
    failure or downstream connection error asks for another attempt. Nothing here
    can block a car: whatever happens to the photo, the session is already
    ``PARKED`` and its ticket pays.
    """

    def __init__(
        self,
        s3: S3Connector,
        sessions: SessionRepository,
        audit: AuditLogger,
        ocr: Any = None,
        normalizer: Any = None,
    ) -> None:
        self._s3 = s3
        self._sessions = sessions
        self._audit = audit
        self._ocr = ocr if ocr is not None else extract_text
        self._normalizer = normalizer if normalizer is not None else normalize_plate

    def handle(self, body: str, attempt: int) -> Result:
        """Process one message end-to-end and say what to do with it.

        ``attempt`` is the 1-based delivery attempt. Domain outcomes come back as
        a :class:`Result`; this method never raises: connector exceptions are
        mapped to ``RETAIN`` per the taxonomy.

        Giving up on a message (a failure on attempt :data:`MAX_ATTEMPTS`) audits
        the failure reason once and also closes the plate reading: the session
        goes ``PENDING`` -> ``UNREADABLE`` whatever the reason was (bad photo or
        an outage), so nothing shows "reading the plate" forever. The cashier can
        still type the plate, and the ticket works either way.
        """

        try:
            outcome, reason = self._process(body)
        except Exception as exc:  # noqa: BLE001 - the consumer must survive any message
            logger.error(
                "unhandled error processing message: step=process dependency=worker: %s", exc
            )
            outcome, reason = Outcome.RETAIN, "unhandled error"

        if outcome is Outcome.RETAIN and attempt >= MAX_ATTEMPTS:
            reason = reason or "processing failed"
            parsed = parse_session_message(body)
            session_id = parsed.message.session_id if parsed.message else "unknown"
            logger.warning(
                "poison message (session=%s) after %d attempts: %s", session_id, attempt, reason
            )
            self._log_poison_best_effort(session_id, reason)
            if parsed.message is not None:
                self._mark_unreadable_best_effort(session_id, reason)
            return Result(Outcome.POISON, reason)

        return Result(outcome, reason)

    def _process(self, body: str) -> tuple[Outcome, str | None]:
        """Drive one message body through the pipeline.

        Returns the outcome plus, for ``RETAIN``, the failure reason that is
        audited if this turns out to be the message's last attempt.
        """

        # 1. Parse + validate the Session_Message. A malformed body is retried
        # like any failure; its poison reason is audited on the last attempt.
        parsed = parse_session_message(body)
        if not parsed.ok or parsed.message is None:
            reason = parsed.reason or "invalid message"
            logger.warning("invalid message: %s", reason)
            return Outcome.RETAIN, reason

        message = parsed.message
        session_id = message.session_id

        # 2. Look up the session by id. A lookup failure (RDS unreachable) must
        # NOT acknowledge the message: it is retried.
        try:
            row = self._sessions.get(session_id)
        except Exception as exc:  # noqa: BLE001 - map any lookup failure to RETAIN
            logger.error(
                "session lookup failed for %s: dependency=RDS: %s",
                session_id,
                exc,
            )
            return Outcome.RETAIN, "session lookup failed (RDS)"

        # 3. Idempotency branch. A missing session (the cashier deleted it) or a
        # plate that is no longer PENDING (already read, already given up on, or
        # typed by the cashier) is terminal: acknowledge without side effects.
        # The session status is not looked at.
        if row is None:
            logger.info("session %s not found; acknowledging message", session_id)
            return Outcome.DELETE, None
        if row.plate_status != PLATE_PENDING:
            logger.info(
                "session %s plate already %s; acknowledging message (idempotent)",
                session_id,
                row.plate_status,
            )
            return Outcome.DELETE, None

        # 4. Download the photo. Any retrieval error is retried.
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

        # 5. OCR extraction. Domain failures come back as an OcrResult with
        # ok=False; retry and log the step.
        ocr_result: OcrResult = self._ocr(image_bytes)
        if not ocr_result.ok or ocr_result.raw_text is None:
            logger.warning(
                "OCR failed for %s: error=%s",
                session_id,
                ocr_result.error,
            )
            return Outcome.RETAIN, f"OCR failed: {ocr_result.error}"

        # 6. Normalize the raw text into a canonical plate. OCR is deterministic,
        # so an unreadable plate would fail again on every attempt: the photo is
        # recorded as UNREADABLE at once and the message is acknowledged. The
        # session is untouched (still PARKED, ticket valid); the cashier may type
        # the plate if they want to. Only a failed RDS update retries.
        plate_result: PlateResult = self._normalizer(
            ocr_result.raw_text, mercosul=ocr_result.mercosul
        )
        if not plate_result.ok or plate_result.plate is None:
            reason = f"unreadable plate: {plate_result.reason}"
            logger.warning("%s for %s; recording plate as UNREADABLE", reason, session_id)
            try:
                marked = self._sessions.mark_unreadable(session_id)
            except Exception as exc:  # noqa: BLE001 - keep the message for a retry
                logger.error(
                    "failed to mark the plate of %s as UNREADABLE: dependency=RDS: %s",
                    session_id,
                    exc,
                )
                return Outcome.RETAIN, "session update failed (RDS)"
            if marked:
                self._log_failed_best_effort(session_id, reason)
            return Outcome.DELETE, None

        # 7. Side effects. The plate is readable and still PENDING, so record it
        # and audit the reading.
        return self._apply_side_effects(session_id, plate_result.plate)

    def _apply_side_effects(self, session_id: str, plate: str) -> tuple[Outcome, str | None]:
        """Record the plate and audit it in one RDS transaction.

        The RDS conditional ``UPDATE`` is the idempotency gate, and its
        transaction stays open while the audit entry is written:

          1. ``UPDATE ... SET plate_status='READ' WHERE plate_status='PENDING'``
             inside a transaction. Zero rows updated means another delivery
             already recorded a result, or the cashier typed the plate in the
             meantime, so nothing else runs and the message is acknowledged.
          2. DynamoDB ``PutItem`` with ``action=OCR_PROCESSING``.
          3. ``COMMIT``.

        If any step fails, the transaction rolls back so the row stays
        ``PENDING`` and the message is retried: the next attempt re-runs the whole
        pipeline instead of finding a ``READ`` row that was never audited. The
        only effect that cannot be undone is an audit entry written right before
        a failed COMMIT; that leaves an extra ``OCR_PROCESSING`` entry, never a
        missing one.

        The spots counter is not involved: the API took the spot when the car
        came in.
        """

        step = "RDS"
        try:
            with self._sessions.plate_transition(session_id, plate) as transitioned:
                if not transitioned:
                    logger.info(
                        "session %s plate no longer PENDING at update time; "
                        "skipping side effects, acknowledging message",
                        session_id,
                    )
                    return Outcome.DELETE, None

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
            return Outcome.RETAIN, f"{step} failed"

        logger.info("session %s plate read: %s", session_id, plate)
        return Outcome.DELETE, None

    def _log_poison_best_effort(self, session_id: str, reason: str) -> None:
        """Record a poison classification reason without blocking.

        ``AuditLogger`` already retries the write up to 3 times; a final failure
        is logged and swallowed so the consumer keeps going.
        """

        try:
            self._audit.log_poison(session_id, reason)
        except Exception as exc:  # noqa: BLE001 - poison audit is best-effort
            logger.error(
                "failed to record poison reason for %s: %s",
                session_id,
                exc,
            )

    def _mark_unreadable_best_effort(self, session_id: str, reason: str = "") -> None:
        """Close a plate reading that will not be retried and audit it (best-effort)."""
        try:
            if self._sessions.mark_unreadable(session_id):
                self._log_failed_best_effort(session_id, reason)
        except Exception as exc:  # noqa: BLE001 - best-effort status update
            logger.error(
                "failed to mark the plate of session %s as UNREADABLE in RDS: %s",
                session_id,
                exc,
            )

    def _log_failed_best_effort(self, session_id: str, reason: str) -> None:
        """Record an OCR_FAILED audit entry without blocking."""
        try:
            self._audit.log_failed(session_id, reason)
        except Exception as exc:  # noqa: BLE001 - best-effort audit
            logger.error(
                "failed to record failed audit entry for %s: %s",
                session_id,
                exc,
            )

    def close(self) -> None:
        """Close the RDS/S3/DynamoDB connections, surviving individual failures.

        Each connector is closed independently; a failure closing one is logged
        with the failed dependency and the routine proceeds to the next, so no
        single close error prevents releasing the others. Connectors without a
        ``close`` are skipped.
        """

        for connector, dependency in (
            (self._sessions, "RDS"),
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
