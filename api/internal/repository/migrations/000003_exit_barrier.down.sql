-- Back to the single-step exit: a PAID car is considered gone. A car that paid
-- but had not crossed the barrier yet loses its spot in the count, so delete
-- the `spots:available` key after this rollback; the API rebuilds it.
DROP INDEX IF EXISTS idx_sessions_plate_occupying;

UPDATE sessions
SET status = 'PAID', exited_at = COALESCE(exited_at, paid_at)
WHERE status IN ('PAID', 'EXITED');

ALTER TABLE sessions DROP COLUMN paid_at;
