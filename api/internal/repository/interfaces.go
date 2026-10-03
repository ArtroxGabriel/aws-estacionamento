package repository

import (
	"context"
	"io"
	"time"

	"api/internal/model"
)

type SessionRepository interface {
	Create(ctx context.Context, session *model.Session) error
	GetByID(ctx context.Context, id string) (*model.Session, error)
	MarkAsPaid(ctx context.Context, id string, exitedAt time.Time, amount float64) (*model.Session, error)
	MarkAsFailed(ctx context.Context, id string) error
	CountActive(ctx context.Context) (int64, error)
	FindAll(ctx context.Context, status *string, plate *string) ([]*model.Session, error)
}

type SpotsRepository interface {
	GetAvailable(ctx context.Context) (int64, error)
	SetAvailable(ctx context.Context, count int64) error
	Increment(ctx context.Context) (int64, error)
	Decrement(ctx context.Context) (int64, error)
}

type BlobStorage interface {
	Upload(ctx context.Context, key string, body io.Reader, contentType string) error
	Download(ctx context.Context, key string) (io.ReadCloser, string, error)
}

type EventPublisher interface {
	Publish(ctx context.Context, payload any) error
}

type AuditLogger interface {
	LogEvent(ctx context.Context, action, entityID string, details map[string]any) error
	GetRecentLogs(ctx context.Context, limit int) ([]*model.AuditLog, error)
}
