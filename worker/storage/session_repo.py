"""Session_Repository — RDS (PostgreSQL) session persistence.

Reads and advances rows of the shared ``sessions`` table using ``psycopg`` (v3)
against the ``DATABASE_URL`` connection string. This module honors the exact
schema owned by the Go API (``api/internal/repository/migrations``): it only
ever reads/writes the ``id``, ``license_plate``, ``status`` and ``s3_photo_key``
columns and never invents new names.

The ``mark_parked`` conditional ``UPDATE`` is the single atomic idempotency gate
behind "exactly-once processing": it advances a row from ``PROCESSING`` to
``PARKED`` and returns ``True`` iff exactly one row was updated. A redelivered
message whose row already advanced updates zero rows, so ``mark_parked`` returns
``False`` and the Poller skips the Redis decrement.

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 11.5.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

__all__ = ["SessionRow", "SessionRepository"]


@dataclass
class SessionRow:
    """A projection of the shared ``sessions`` schema used by the Worker.

    Mirrors the columns owned/relevant to the Worker exactly as defined by the
    API migration ``000001_create_sessions_table`` (``id VARCHAR(64)``,
    ``license_plate VARCHAR(16) NULL``, ``status VARCHAR(20) NOT NULL``,
    ``s3_photo_key TEXT NOT NULL``). Columns owned solely by the API
    (``entered_at``, ``exited_at``, ``amount_paid``) are intentionally omitted.
    """

    id: str
    license_plate: str | None
    status: str  # PROCESSING | PARKED | PAID
    s3_photo_key: str


class SessionRepository:
    """Reads and conditionally advances ``sessions`` rows in RDS (PostgreSQL).

    Uses ``psycopg`` (v3) against the ``DATABASE_URL`` supplied at construction
    (Req 6.2). Connections are opened per operation so a transient failure never
    leaves a poisoned long-lived handle; on any connection/query error the
    method reports failure without mutating the record (Req 6.5).
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def get(self, session_id: str) -> SessionRow | None:
        """Return the ``SessionRow`` for ``session_id`` or ``None`` if absent.

        Reads ``id``, ``license_plate``, ``status`` and ``s3_photo_key`` for the
        row identified by ``session_id``. Returns ``None`` when no row matches
        (Req 6.3). Propagates ``psycopg`` errors on a connection/query failure so
        the Poller can retain the message for redelivery (Req 11.4).
        """
        with psycopg.connect(self._database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, license_plate, status, s3_photo_key "
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
        )

    def mark_parked(self, session_id: str, plate: str) -> bool:
        """Conditionally advance a session from ``PROCESSING`` to ``PARKED``.

        Runs the atomic idempotency gate::

            UPDATE sessions SET license_plate=%s, status='PARKED'
            WHERE id=%s AND status='PROCESSING'

        Returns ``True`` iff exactly one row was updated (Req 6.1, 6.6, 11.5).
        A row that does not exist or is not in ``PROCESSING`` (e.g. already
        ``PARKED``/``PAID`` on redelivery) updates zero rows, so this returns
        ``False`` and the record is left unchanged (Req 6.3, 6.6). On any
        connection/query error the ``psycopg`` exception propagates and, because
        the connection context manager rolls back, the record is left unchanged
        with no advance to ``PARKED`` (Req 6.5).
        """
        with psycopg.connect(self._database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sessions SET license_plate=%s, status='PARKED' "
                    "WHERE id=%s AND status='PROCESSING'",
                    (plate, session_id),
                )
                updated = cur.rowcount

        # True iff exactly one row transitioned PROCESSING -> PARKED.
        return updated == 1
