-- The ticket identifies the car; reading the plate only enriches the session
-- (docs/PLANO-FOTO-OPCIONAL.md). `status` now says where the car is
-- (PARKED -> PAID) and `plate_status` says how the plate reading went
-- (PENDING -> READ | UNREADABLE, or MANUAL when the cashier types it).
ALTER TABLE sessions ADD COLUMN plate_status VARCHAR(16) NOT NULL DEFAULT 'PENDING';

UPDATE sessions SET plate_status = 'READ' WHERE license_plate IS NOT NULL;

-- Nothing is pending for a session whose OCR failed or that was already paid
-- without a plate.
UPDATE sessions
SET plate_status = 'UNREADABLE'
WHERE license_plate IS NULL AND status IN ('FAILED', 'PAID');

-- PROCESSING and FAILED cars are inside the lot: they become PARKED. They did
-- not hold a spot in the Redis counter before, so delete the `spots:available`
-- key after this migration; the API rebuilds it from this table.
UPDATE sessions SET status = 'PARKED' WHERE status IN ('PROCESSING', 'FAILED');
