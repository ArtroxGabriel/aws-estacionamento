"""Session_Repository — RDS (PostgreSQL) session persistence.

Reads rows of the shared ``sessions`` table and records the plate read from
the entry photo, using ``psycopg`` (v3) against the ``DATABASE_URL`` connection
string. This module honors the exact schema owned by the Go API
(``api/internal/repository/migrations``): it reads ``id``, ``license_plate``,
``status``, ``plate_status`` and ``s3_photo_key``, and writes only
``license_plate`` and ``plate_status``.

The Worker never touches ``status``: the session is ``PARKED`` and holds its
spot from the moment the API creates it, because the ticket (the session id) is
what identifies the car. The plate only enriches the session.

``plate_status = 'PENDING'`` is the single atomic idempotency gate behind
"exactly-once processing": both writes are conditional on it, so a redelivered
message, a plate the cashier already typed (``MANUAL``) or a result recorded by
another instance update zero rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg

__all__ = [
    "PLATE_MANUAL",
    "PLATE_PENDING",
    "PLATE_READ",
    "PLATE_UNREADABLE",
    "SessionRow",
    "SessionRepository",
]

# Bounds how long an unreachable RDS can stall one message, keeping the whole
# pipeline well inside the queue visibility timeout (Req 9.4).
CONNECT_TIMEOUT_SECONDS = 5

# ``plate_status`` values — shared contract with the Go API
# (``api/internal/model/session.go``).
PLATE_PENDING = "PENDING"  # waiting for this Worker
PLATE_READ = "READ"  # read by OCR
PLATE_UNREADABLE = "UNREADABLE"  # OCR found no plate / photo could not be processed
PLATE_MANUAL = "MANUAL"  # typed by the cashier


@dataclass
class SessionRow:
    """A projection of the shared ``sessions`` schema used by the Worker.

    Mirrors the columns relevant to the Worker exactly as defined by the API
    migrations (``id VARCHAR(64)``, ``license_plate VARCHAR(16) NULL``,
    ``status VARCHAR(20) NOT NULL``, ``plate_status VARCHAR(16) NOT NULL``,
    ``s3_photo_key TEXT NOT NULL``). Columns owned solely by the API
    (``entered_at``, ``exited_at``, ``amount_paid``) are intentionally omitted.
    """

    id: str
    license_plate: str | None
    status: str  # PARKED | PAID — owned by the API, read-only here
    s3_photo_key: str
    plate_status: str = PLATE_PENDING  # PENDING | READ | UNREADABLE | MANUAL


class SessionRepository:
    """Reads ``sessions`` rows and records the OCR result in RDS (PostgreSQL).

    Uses ``psycopg`` (v3) against the ``DATABASE_URL`` supplied at construction
    (Req 6.2). Connections are opened per operation so a transient failure never
    leaves a poisoned long-lived handle; on any connection/query error the
    method reports failure without mutating the record (Req 6.5).
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def get(self, session_id: str) -> SessionRow | None:
        """Return the ``SessionRow`` for ``session_id`` or ``None`` if absent.

        Returns ``None`` when no row matches (Req 6.3). Propagates ``psycopg``
        errors on a connection/query failure so the Poller can retain the
        message for redelivery (Req 11.4).
        """
        with psycopg.connect(self._database_url, connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, license_plate, status, s3_photo_key, plate_status "
                    "FROM sessions WHERE id = %s",
                    (session_id,),
                )
                row = cur.fetchone()

        if row is None:
            return None

        return SessionRow(
            id=row[0],
            license_plate=row[1],
            status=row[2],
            s3_photo_key=row[3],
            plate_status=row[4],
        )

    @contextmanager
    def plate_transition(self, session_id: str, plate: str) -> Iterator[bool]:
        """Record the plate read by OCR inside an open transaction that commits
        only when the ``with`` body succeeds.

        Runs the atomic idempotency gate::

            UPDATE sessions SET license_plate=%s, plate_status='READ'
            WHERE id=%s AND plate_status='PENDING'

        and yields ``True`` iff exactly one row was updated. A row that does
        not exist or is no longer ``PENDING`` (already read on a redelivery, or
        typed by the cashier meanwhile) updates zero rows and yields ``False``,
        so a plate typed by a person is never overwritten by the OCR.

        ``status`` is not part of the gate on purpose: a car that already paid
        and left before its photo was read still gets its plate recorded, since
        the plate is evidence for the audit trail.

        The caller writes the audit entry inside the ``with`` block. If that
        raises, the transaction rolls back and the row stays ``PENDING``, so the
        redelivered message re-runs the whole pipeline instead of finding a
        ``READ`` row that was never audited. The ``UPDATE`` holds the row lock
        until COMMIT, so a concurrent delivery on another instance blocks and
        then updates zero rows. A COMMIT failure propagates from the ``with``
        exit.
        """
        with psycopg.connect(self._database_url, connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sessions SET license_plate=%s, plate_status='READ' "
                    "WHERE id=%s AND plate_status='PENDING'",
                    (plate, session_id),
                )
                transitioned = cur.rowcount == 1
            yield transitioned

    def mark_unreadable(self, session_id: str) -> bool:
        """Record that no plate will come out of this photo.

        Only a session still ``PENDING`` changes; returns ``True`` iff one row
        was updated. The session itself is untouched: it stays ``PARKED`` (or
        ``PAID``) and its ticket keeps working.
        """
        with psycopg.connect(self._database_url, connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sessions SET plate_status = 'UNREADABLE' "
                    "WHERE id = %s AND plate_status = 'PENDING'",
                    (session_id,),
                )
                return cur.rowcount == 1
