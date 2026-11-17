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

const sessionColumns = `id, license_plate, status, plate_status, s3_photo_key, entered_at, paid_at, exited_at, amount_paid`

type rowScanner interface {
	Scan(dest ...any) error
}

func scanSession(row rowScanner) (*model.Session, error) {
	var s model.Session
	err := row.Scan(
		&s.ID,
		&s.LicensePlate,
		&s.Status,
		&s.PlateStatus,
		&s.S3PhotoKey,
		&s.EnteredAt,
		&s.PaidAt,
		&s.ExitedAt,
		&s.AmountPaid,
	)
	if err != nil {
		return nil, err
	}
	return &s, nil
}

// scanOptionalSession maps "no row" to a nil session, as the callers expect
// for a missing session or a conditional UPDATE that matched nothing.
func scanOptionalSession(row rowScanner) (*model.Session, error) {
	s, err := scanSession(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return s, err
}

func (r *PostgresSessionRepo) Create(ctx context.Context, s *model.Session) error {
	query := `
		INSERT INTO sessions (id, license_plate, status, plate_status, s3_photo_key, entered_at)
		VALUES ($1, $2, $3, $4, $5, $6)
	`
	_, err := r.db.ExecContext(ctx, query, s.ID, s.LicensePlate, s.Status, s.PlateStatus, s.S3PhotoKey, s.EnteredAt)
	return err
}

func (r *PostgresSessionRepo) GetByID(ctx context.Context, id string) (*model.Session, error) {
	query := `SELECT ` + sessionColumns + ` FROM sessions WHERE id = $1`
	return scanOptionalSession(r.db.QueryRowContext(ctx, query, id))
}

func (r *PostgresSessionRepo) MarkAsPaid(ctx context.Context, id string, paidAt time.Time, amount float64) (*model.Session, error) {
	query := `
		UPDATE sessions
		SET status = 'PAID', paid_at = $2, amount_paid = $3
		WHERE id = $1 AND status = 'PARKED'
		RETURNING ` + sessionColumns
	return scanOptionalSession(r.db.QueryRowContext(ctx, query, id, paidAt, amount))
}

// MarkAsExited is the only way a spot leaves the lot through the barrier:
// the WHERE makes two racing exits for the same car change one row only.
func (r *PostgresSessionRepo) MarkAsExited(ctx context.Context, id string, exitedAt time.Time) (*model.Session, error) {
	query := `
		UPDATE sessions
		SET status = 'EXITED', exited_at = $2
		WHERE id = $1 AND status = 'PAID'
		RETURNING ` + sessionColumns
	return scanOptionalSession(r.db.QueryRowContext(ctx, query, id, exitedAt))
}

func (r *PostgresSessionRepo) FindOccupyingByPlate(ctx context.Context, plate string) ([]*model.Session, error) {
	query := `SELECT ` + sessionColumns + ` FROM sessions
		WHERE license_plate = $1
		  AND plate_status IN ('READ', 'MANUAL')
		  AND status IN ('PARKED', 'PAID')
		ORDER BY paid_at ASC NULLS LAST, entered_at ASC`
	return r.querySessions(ctx, query, plate)
}

func (r *PostgresSessionRepo) UpdatePlate(ctx context.Context, id, plate string) (*model.Session, error) {
	query := `
		UPDATE sessions
		SET license_plate = $2, plate_status = 'MANUAL'
		WHERE id = $1 AND status IN ('PARKED', 'PAID')
		RETURNING ` + sessionColumns
	return scanOptionalSession(r.db.QueryRowContext(ctx, query, id, plate))
}

func (r *PostgresSessionRepo) Delete(ctx context.Context, id string) (bool, error) {
	res, err := r.db.ExecContext(ctx, `DELETE FROM sessions WHERE id = $1`, id)
	if err != nil {
		return false, err
	}
	n, err := res.RowsAffected()
	if err != nil {
		return false, err
	}
	return n == 1, nil
}

func (r *PostgresSessionRepo) MarkPlateUnreadable(ctx context.Context, id string) error {
	query := `
		UPDATE sessions
		SET plate_status = 'UNREADABLE'
		WHERE id = $1 AND plate_status = 'PENDING'
	`
	_, err := r.db.ExecContext(ctx, query, id)
	return err
}

// CountActive counts the sessions that hold a spot in the Redis counter. A
// spot is taken at the entry (every session starts PARKED) and given back at
// the exit barrier, so PARKED and PAID sessions hold one.
func (r *PostgresSessionRepo) CountActive(ctx context.Context) (int64, error) {
	query := `SELECT COUNT(*) FROM sessions WHERE status IN ('PARKED', 'PAID')`
	var count int64
	err := r.db.QueryRowContext(ctx, query).Scan(&count)
	return count, err
}

func (r *PostgresSessionRepo) FindAll(ctx context.Context, filter SessionFilter) ([]*model.Session, error) {
	query := `SELECT ` + sessionColumns + ` FROM sessions WHERE 1=1`
	var args []any
	for _, cond := range []struct {
		column string
		value  string
	}{
		{"status", filter.Status},
		{"license_plate", filter.Plate},
		{"plate_status", filter.PlateStatus},
	} {
		if cond.value == "" {
			continue
		}
		args = append(args, cond.value)
		query += fmt.Sprintf(` AND %s = $%d`, cond.column, len(args))
	}
	query += ` ORDER BY entered_at DESC`
	return r.querySessions(ctx, query, args...)
}

func (r *PostgresSessionRepo) querySessions(ctx context.Context, query string, args ...any) ([]*model.Session, error) {
	rows, err := r.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var sessions []*model.Session
	for rows.Next() {
		s, err := scanSession(rows)
		if err != nil {
			return nil, err
		}
		sessions = append(sessions, s)
	}
	return sessions, rows.Err()
}
