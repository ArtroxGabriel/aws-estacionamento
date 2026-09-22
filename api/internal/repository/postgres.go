package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"api/internal/model"
)

type PostgresSessionRepo struct {
	db *sql.DB
}

var _ SessionRepository = (*PostgresSessionRepo)(nil)

func NewPostgresSessionRepo(db *sql.DB) *PostgresSessionRepo {
	return &PostgresSessionRepo{db: db}
}

func (r *PostgresSessionRepo) Create(ctx context.Context, s *model.Session) error {
	query := `
		INSERT INTO sessions (id, license_plate, status, s3_photo_key, entered_at)
		VALUES ($1, $2, $3, $4, $5)
	`
	_, err := r.db.ExecContext(ctx, query, s.ID, s.LicensePlate, s.Status, s.S3PhotoKey, s.EnteredAt)
	return err
}

func (r *PostgresSessionRepo) GetByID(ctx context.Context, id string) (*model.Session, error) {
	query := `
		SELECT id, license_plate, status, s3_photo_key, entered_at, exited_at, amount_paid
		FROM sessions WHERE id = $1
	`
	var s model.Session
	err := r.db.QueryRowContext(ctx, query, id).Scan(
		&s.ID,
		&s.LicensePlate,
		&s.Status,
		&s.S3PhotoKey,
		&s.EnteredAt,
		&s.ExitedAt,
		&s.AmountPaid,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &s, nil
}

func (r *PostgresSessionRepo) MarkAsPaid(ctx context.Context, id string, exitedAt time.Time, amount float64) (*model.Session, error) {
	query := `
		UPDATE sessions
		SET status = 'PAID', exited_at = $2, amount_paid = $3
		WHERE id = $1
		RETURNING id, license_plate, status, s3_photo_key, entered_at, exited_at, amount_paid
	`
	var s model.Session
	err := r.db.QueryRowContext(ctx, query, id, exitedAt, amount).Scan(
		&s.ID,
		&s.LicensePlate,
		&s.Status,
		&s.S3PhotoKey,
		&s.EnteredAt,
		&s.ExitedAt,
		&s.AmountPaid,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &s, nil
}
