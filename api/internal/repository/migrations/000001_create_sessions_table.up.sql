CREATE TABLE IF NOT EXISTS sessions (
    id VARCHAR(64) PRIMARY KEY,
    license_plate VARCHAR(16) NULL,
    status VARCHAR(20) NOT NULL,
    s3_photo_key TEXT NOT NULL,
    entered_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    exited_at TIMESTAMP WITH TIME ZONE NULL,
    amount_paid NUMERIC(10, 2) NULL
);
