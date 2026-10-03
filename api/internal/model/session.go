package model

import "time"

type Session struct {
	ID           string     `json:"id"`
	LicensePlate *string    `json:"license_plate,omitempty"`
	Status       string     `json:"status"`
	S3PhotoKey   string     `json:"s3_photo_key"`
	EnteredAt    time.Time  `json:"entered_at"`
	ExitedAt     *time.Time `json:"exited_at,omitempty"`
	AmountPaid   *float64   `json:"amount_paid,omitempty"`
}

// ActiveSession is a Session enriched with the amount currently due.
// The embedded Session is flattened in the JSON output.
type ActiveSession struct {
	Session
	AmountDue float64 `json:"amount_due"`
}
