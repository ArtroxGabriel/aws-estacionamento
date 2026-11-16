UPDATE sessions SET status = 'FAILED' WHERE status = 'PARKED' AND plate_status = 'UNREADABLE';
UPDATE sessions SET status = 'PROCESSING' WHERE status = 'PARKED' AND plate_status = 'PENDING';

ALTER TABLE sessions DROP COLUMN plate_status;
