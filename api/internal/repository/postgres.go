package repository

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
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
		WHERE id = $1 AND status IN ('PARKED', 'FAILED')
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

func (r *PostgresSessionRepo) MarkAsFailed(ctx context.Context, id string) error {
	query := `
		UPDATE sessions
		SET status = 'FAILED'
		WHERE id = $1 AND status = 'PROCESSING'
	`
	_, err := r.db.ExecContext(ctx, query, id)
	return err
}

func (r *PostgresSessionRepo) CountActive(ctx context.Context) (int64, error) {
	query := `SELECT COUNT(*) FROM sessions WHERE status IN ('PROCESSING', 'PARKED')`
	var count int64
	err := r.db.QueryRowContext(ctx, query).Scan(&count)
	return count, err
}

func (r *PostgresSessionRepo) FindAll(ctx context.Context, status *string, plate *string) ([]*model.Session, error) {
	query := `
		SELECT id, license_plate, status, s3_photo_key, entered_at, exited_at, amount_paid
		FROM sessions
		WHERE 1=1
	`
	var args []any
	argID := 1

	if status != nil && *status != "" {
		query += ` AND status = $` + fmt.Sprintf("%d", argID)
		args = append(args, *status)
		argID++
	}

	if plate != nil && *plate != "" {
		query += ` AND license_plate = $` + fmt.Sprintf("%d", argID)
		args = append(args, *plate)
		argID++
	}

	query += ` ORDER BY entered_at DESC`

	rows, err := r.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var sessions []*model.Session
	for rows.Next() {
		var s model.Session
		err := rows.Scan(
			&s.ID,
			&s.LicensePlate,
			&s.Status,
			&s.S3PhotoKey,
			&s.EnteredAt,
			&s.ExitedAt,
			&s.AmountPaid,
		)
		if err != nil {
			return nil, err
		}
		sessions = append(sessions, &s)
	}
	return sessions, rows.Err()
}
