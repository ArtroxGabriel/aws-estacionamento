package model

import "time"

// Session status: where the car is. The ticket (ID) is what identifies it.
// PARKED and PAID cars are inside the lot and hold a spot; the spot is given
// back when the car crosses the exit barrier (EXITED).
const (
	StatusParked = "PARKED" // inside, not paid
	StatusPaid   = "PAID"   // inside, paid, allowed to leave
	StatusExited = "EXITED" // crossed the exit barrier
)

// Plate status: how reading the plate from the entry photo went. It never
// blocks the session; the plate is only evidence and a shortcut to find it.
const (
	PlatePending    = "PENDING"    // waiting for the worker
	PlateRead       = "READ"       // read by OCR
	PlateUnreadable = "UNREADABLE" // OCR found no plate, or the photo was never queued
	PlateManual     = "MANUAL"     // typed by the cashier
)

type Session struct {
	ID           string     `json:"id"`
	LicensePlate *string    `json:"license_plate,omitempty"`
	Status       string     `json:"status"`
	PlateStatus  string     `json:"plate_status"`
	S3PhotoKey   string     `json:"s3_photo_key"`
	EnteredAt    time.Time  `json:"entered_at"`
	PaidAt       *time.Time `json:"paid_at,omitempty"`
	ExitedAt     *time.Time `json:"exited_at,omitempty"`
	AmountPaid   *float64   `json:"amount_paid,omitempty"`
	// AmountDue is what the cashier would charge right now. It is only filled in
	// by listings of PARKED sessions and never stored.
	AmountDue *float64 `json:"amount_due,omitempty"`
}

// Occupying reports whether the session holds a spot in the lot.
func (s *Session) Occupying() bool {
	return s.Status == StatusParked || s.Status == StatusPaid
}

// KnownPlate returns the plate when it can be trusted to find the car: read
// by the OCR or typed by the cashier.
func (s *Session) KnownPlate() (string, bool) {
	if s.LicensePlate == nil || (s.PlateStatus != PlateRead && s.PlateStatus != PlateManual) {
		return "", false
	}
	return *s.LicensePlate, true
}
