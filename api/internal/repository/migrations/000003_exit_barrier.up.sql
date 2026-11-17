-- Two barriers (.kiro/specs/api-rabbitmq-exit-barrier): paying no longer frees
-- the spot. `status` is now PARKED (inside, not paid) -> PAID (inside, paid)
-- -> EXITED (crossed the exit barrier). `paid_at` records the payment and
-- `exited_at` now means "crossed the exit barrier".
ALTER TABLE sessions ADD COLUMN paid_at TIMESTAMP WITH TIME ZONE NULL;

-- Under the previous rule a PAID car had already left and already gave its
-- spot back, so it is EXITED now. The spots counter stays valid: only PARKED
-- sessions held a spot before and only PARKED/PAID sessions hold one now.
UPDATE sessions
SET paid_at = exited_at, status = 'EXITED'
WHERE status = 'PAID';

-- The exit barrier looks cars up by plate among those still inside.
CREATE INDEX IF NOT EXISTS idx_sessions_plate_occupying
    ON sessions (license_plate)
    WHERE status IN ('PARKED', 'PAID');
