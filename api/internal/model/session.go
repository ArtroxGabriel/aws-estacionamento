package model

import "time"

// Session status: where the car is. The ticket (ID) is what identifies it.
const (
	StatusParked = "PARKED"
	StatusPaid   = "PAID"
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
	ExitedAt     *time.Time `json:"exited_at,omitempty"`
	AmountPaid   *float64   `json:"amount_paid,omitempty"`
}
