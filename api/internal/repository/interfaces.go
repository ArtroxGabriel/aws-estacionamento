package repository

import (
	"context"
	"errors"
	"io"
	"time"

	"api/internal/model"
)

// ErrBlobNotFound is returned by BlobStorage.Download for a missing object.
var ErrBlobNotFound = errors.New("blob not found")

// SessionFilter narrows FindAll; an empty field does not filter.
type SessionFilter struct {
	Status      string
	Plate       string
	PlateStatus string
}

type SessionRepository interface {
	Create(ctx context.Context, session *model.Session) error
	GetByID(ctx context.Context, id string) (*model.Session, error)
	// MarkAsPaid moves a PARKED session to PAID; nil when the session is
	// missing or in another status. The car keeps its spot.
	MarkAsPaid(ctx context.Context, id string, paidAt time.Time, amount float64) (*model.Session, error)
	// MarkAsExited moves a PAID session to EXITED in one conditional update;
	// nil when the session is missing or not PAID (unpaid, or another request
	// already opened the barrier).
	MarkAsExited(ctx context.Context, id string, exitedAt time.Time) (*model.Session, error)
	// FindOccupyingByPlate lists the sessions inside the lot (PARKED or PAID)
	// whose plate was read or typed (READ/MANUAL) and equals plate, the
	// oldest payment first and the unpaid ones last.
	FindOccupyingByPlate(ctx context.Context, plate string) ([]*model.Session, error)
	// MarkPlateUnreadable records that no OCR result will come for a session
	// still waiting for one (plate_status PENDING).
	MarkPlateUnreadable(ctx context.Context, id string) error
	// UpdatePlate sets the plate typed by the cashier on a session still
	// inside the lot (PARKED or PAID, plate_status MANUAL); nil when the
	// session is missing or already EXITED.
	UpdatePlate(ctx context.Context, id, plate string) (*model.Session, error)
	Delete(ctx context.Context, id string) (bool, error)
	// CountActive counts the sessions holding a spot (PARKED and PAID).
	CountActive(ctx context.Context) (int64, error)
	FindAll(ctx context.Context, filter SessionFilter) ([]*model.Session, error)
}

type SpotsRepository interface {
	GetAvailable(ctx context.Context) (int64, error)
	SetAvailable(ctx context.Context, count int64) error
	Increment(ctx context.Context) (int64, error)
	Decrement(ctx context.Context) (int64, error)
	// Reserve takes one spot atomically: true when taken, false when the lot
	// is full. It errors when the counter is missing or Redis is unreachable,
	// and never creates the key.
	Reserve(ctx context.Context) (bool, error)
}

type BlobStorage interface {
	Upload(ctx context.Context, key string, body io.Reader, contentType string) error
	Download(ctx context.Context, key string) (io.ReadCloser, string, error)
	Delete(ctx context.Context, key string) error
}

type EventPublisher interface {
	Publish(ctx context.Context, payload any) error
}

type AuditLogger interface {
	LogEvent(ctx context.Context, action, entityID string, details map[string]any) error
	GetRecentLogs(ctx context.Context, limit int) ([]*model.AuditLog, error)
}
