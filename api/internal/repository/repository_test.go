package repository_test

import (
	"context"
	"database/sql"
	"errors"
	"os"
	"testing"
	"time"

	"api/internal/model"
	"api/internal/repository"

	_ "github.com/lib/pq"
	"github.com/redis/go-redis/v9"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"
	tcredis "github.com/testcontainers/testcontainers-go/modules/redis"
)

func TestMigrationsAndPostgresSessionRepo_Integration(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping integration test in short mode")
	}

	ctx := context.Background()

	// 1. Start PostgreSQL via Testcontainers
	pgContainer, err := tcpostgres.Run(ctx,
		"postgres:16-alpine",
		tcpostgres.WithDatabase("test_estacionamento"),
		tcpostgres.WithUsername("test_user"),
		tcpostgres.WithPassword("test_pass"),
		tcpostgres.BasicWaitStrategies(),
	)
	if err != nil {
		t.Fatalf("failed to start postgres container: %v", err)
	}
	defer func() {
		_ = pgContainer.Terminate(ctx)
	}()

	connStr, err := pgContainer.ConnectionString(ctx, "sslmode=disable")
	if err != nil {
		t.Fatalf("failed to get connection string: %v", err)
	}

	db, err := sql.Open("postgres", connStr)
	if err != nil {
		t.Fatalf("failed to connect to postgres: %v", err)
	}
	defer db.Close()

	// 2. Legacy data, as the previous model left it: the table of migration
	// 000001 with sessions in PROCESSING/FAILED. Migration 000002 must turn
	// them into the ticket-first model.
	legacySchema, err := os.ReadFile("migrations/000001_create_sessions_table.up.sql")
	if err != nil {
		t.Fatalf("failed to read migration 000001: %v", err)
	}
	if _, err := db.ExecContext(ctx, string(legacySchema)); err != nil {
		t.Fatalf("failed to create legacy schema: %v", err)
	}
	if _, err := db.ExecContext(ctx, `
		INSERT INTO sessions (id, license_plate, status, s3_photo_key) VALUES
			('legacy-processing', NULL,      'PROCESSING', 'photos/a.jpg'),
			('legacy-failed',     NULL,      'FAILED',     'photos/b.jpg'),
			('legacy-parked',     'ABC1D23', 'PARKED',     'photos/c.jpg'),
			('legacy-paid',       'ABC1234', 'PAID',       'photos/d.jpg'),
			('legacy-paid-blind', NULL,      'PAID',       'photos/e.jpg')
	`); err != nil {
		t.Fatalf("failed to insert legacy sessions: %v", err)
	}

	// 3. Test Migrations (Up)
	t.Run("RunMigrations", func(t *testing.T) {
		err := repository.RunMigrations(db)
		if err != nil {
			t.Fatalf("RunMigrations failed: %v", err)
		}

		// Verify table exists
		var tableName string
		err = db.QueryRow("SELECT tablename FROM pg_tables WHERE tablename = 'sessions'").Scan(&tableName)
		if err != nil || tableName != "sessions" {
			t.Fatalf("expected table 'sessions' to exist, got error: %v", err)
		}

		// Running again is a no-op.
		if err := repository.RunMigrations(db); err != nil {
			t.Fatalf("second RunMigrations failed: %v", err)
		}
	})

	repo := repository.NewPostgresSessionRepo(db)

	t.Run("Migration_ConvertsLegacySessions", func(t *testing.T) {
		want := map[string][2]string{
			"legacy-processing": {"PARKED", "PENDING"},
			"legacy-failed":     {"PARKED", "UNREADABLE"},
			"legacy-parked":     {"PARKED", "READ"},
			"legacy-paid":       {"PAID", "READ"},
			"legacy-paid-blind": {"PAID", "UNREADABLE"},
		}
		for id, expected := range want {
			s, err := repo.GetByID(ctx, id)
			if err != nil || s == nil {
				t.Fatalf("%s: expected session, got %+v, %v", id, s, err)
			}
			if s.Status != expected[0] || s.PlateStatus != expected[1] {
				t.Fatalf("%s: expected %s/%s, got %s/%s", id, expected[0], expected[1], s.Status, s.PlateStatus)
			}
		}
		// Every car that was inside now holds a spot.
		if count, _ := repo.CountActive(ctx); count != 3 {
			t.Fatalf("expected 3 PARKED sessions after the migration, got %d", count)
		}
		if _, err := db.ExecContext(ctx, `DELETE FROM sessions`); err != nil {
			t.Fatalf("failed to clean legacy sessions: %v", err)
		}
	})

	// 4. Test PostgresSessionRepo
	newSession := func(id string) *model.Session {
		return &model.Session{
			ID:          id,
			Status:      model.StatusParked,
			PlateStatus: model.PlatePending,
			S3PhotoKey:  "photos/" + id + ".jpg",
			EnteredAt:   time.Now().UTC().Truncate(time.Millisecond),
		}
	}

	t.Run("CreateAndGetByID", func(t *testing.T) {
		session := newSession("test-pg-session-1")
		if err := repo.Create(ctx, session); err != nil {
			t.Fatalf("failed to create session: %v", err)
		}

		fetched, err := repo.GetByID(ctx, "test-pg-session-1")
		if err != nil {
			t.Fatalf("failed to get session by id: %v", err)
		}
		if fetched == nil {
			t.Fatal("expected session, got nil")
		}
		if fetched.ID != session.ID || fetched.Status != "PARKED" || fetched.PlateStatus != "PENDING" || fetched.LicensePlate != nil {
			t.Fatalf("mismatched session data: %+v", fetched)
		}
	})

	t.Run("CountActive_CountsEveryParkedSession", func(t *testing.T) {
		// A session holds its spot from the entry, plate read or not.
		count, err := repo.CountActive(ctx)
		if err != nil {
			t.Fatalf("CountActive failed: %v", err)
		}
		if count != 1 {
			t.Fatalf("expected the PARKED/PENDING session to be counted, got %d", count)
		}
	})

	t.Run("MarkPlateUnreadable_OnlyWhilePending", func(t *testing.T) {
		if err := repo.Create(ctx, newSession("test-pg-unreadable")); err != nil {
			t.Fatalf("failed to create session: %v", err)
		}
		if err := repo.MarkPlateUnreadable(ctx, "test-pg-unreadable"); err != nil {
			t.Fatalf("MarkPlateUnreadable failed: %v", err)
		}
		s, _ := repo.GetByID(ctx, "test-pg-unreadable")
		if s.Status != "PARKED" || s.PlateStatus != "UNREADABLE" {
			t.Fatalf("expected PARKED/UNREADABLE, got %s/%s", s.Status, s.PlateStatus)
		}

		// A plate the cashier already typed is never overwritten.
		if err := repo.Create(ctx, newSession("test-pg-manual")); err != nil {
			t.Fatalf("failed to create session: %v", err)
		}
		if _, err := repo.UpdatePlate(ctx, "test-pg-manual", "ABC1D23"); err != nil {
			t.Fatalf("failed to update plate: %v", err)
		}
		if err := repo.MarkPlateUnreadable(ctx, "test-pg-manual"); err != nil {
			t.Fatalf("MarkPlateUnreadable failed: %v", err)
		}
		s, _ = repo.GetByID(ctx, "test-pg-manual")
		if s.PlateStatus != "MANUAL" || s.LicensePlate == nil || *s.LicensePlate != "ABC1D23" {
			t.Fatalf("expected MANUAL with ABC1D23 kept, got %s %v", s.PlateStatus, s.LicensePlate)
		}
	})

	t.Run("UpdatePlate_SetsManualAndKeepsParked", func(t *testing.T) {
		updated, err := repo.UpdatePlate(ctx, "test-pg-unreadable", "LSN4I49")
		if err != nil {
			t.Fatalf("failed to update plate: %v", err)
		}
		if updated == nil || updated.Status != "PARKED" || updated.PlateStatus != "MANUAL" || *updated.LicensePlate != "LSN4I49" {
			t.Fatalf("expected PARKED/MANUAL with LSN4I49, got %+v", updated)
		}
	})

	t.Run("FindAll_Filters", func(t *testing.T) {
		ids := func(filter repository.SessionFilter) map[string]bool {
			sessions, err := repo.FindAll(ctx, filter)
			if err != nil {
				t.Fatalf("FindAll failed: %v", err)
			}
			got := map[string]bool{}
			for _, s := range sessions {
				got[s.ID] = true
			}
			return got
		}
		if got := ids(repository.SessionFilter{}); len(got) != 3 {
			t.Fatalf("expected 3 sessions without a filter, got %v", got)
		}
		if got := ids(repository.SessionFilter{PlateStatus: "PENDING"}); len(got) != 1 || !got["test-pg-session-1"] {
			t.Fatalf("expected only the PENDING session, got %v", got)
		}
		if got := ids(repository.SessionFilter{Status: "PARKED", PlateStatus: "MANUAL"}); len(got) != 2 {
			t.Fatalf("expected the 2 MANUAL sessions, got %v", got)
		}
		if got := ids(repository.SessionFilter{Plate: "LSN4I49"}); len(got) != 1 || !got["test-pg-unreadable"] {
			t.Fatalf("expected the session found by plate, got %v", got)
		}
		if got := ids(repository.SessionFilter{Status: "PAID"}); len(got) != 0 {
			t.Fatalf("expected no PAID session yet, got %v", got)
		}
	})

	t.Run("MarkAsPaid_WithoutPlate", func(t *testing.T) {
		exitedAt := time.Now().UTC().Truncate(time.Millisecond)
		amount := 12.50

		// test-pg-session-1 never had its plate read: the ticket alone pays.
		paid, err := repo.MarkAsPaid(ctx, "test-pg-session-1", exitedAt, amount)
		if err != nil {
			t.Fatalf("failed to mark session as paid: %v", err)
		}
		if paid == nil || paid.Status != "PAID" || paid.PlateStatus != "PENDING" {
			t.Fatalf("expected PAID keeping plate_status PENDING, got %+v", paid)
		}
		if paid.AmountPaid == nil || *paid.AmountPaid != amount {
			t.Fatalf("expected amount %f, got %v", amount, paid.AmountPaid)
		}
		if count, _ := repo.CountActive(ctx); count != 2 {
			t.Fatalf("expected 2 PARKED sessions after the payment, got %d", count)
		}
	})

	t.Run("MarkAsPaid_TwiceIsRefused", func(t *testing.T) {
		paid, err := repo.MarkAsPaid(ctx, "test-pg-session-1", time.Now().UTC(), 12.50)
		if err != nil || paid != nil {
			t.Fatalf("expected nil for an already PAID session, got %+v, %v", paid, err)
		}
	})

	t.Run("UpdatePlate_PaidIsNotEditable", func(t *testing.T) {
		updated, err := repo.UpdatePlate(ctx, "test-pg-session-1", "ABC1D23")
		if err != nil || updated != nil {
			t.Fatalf("expected nil for a PAID session, got %+v, %v", updated, err)
		}
	})

	t.Run("Delete", func(t *testing.T) {
		deleted, err := repo.Delete(ctx, "test-pg-manual")
		if err != nil || !deleted {
			t.Fatalf("expected deleted, got %v, %v", deleted, err)
		}
		if fetched, _ := repo.GetByID(ctx, "test-pg-manual"); fetched != nil {
			t.Fatalf("expected session gone, got %+v", fetched)
		}
		if deleted, _ := repo.Delete(ctx, "test-pg-manual"); deleted {
			t.Fatal("expected second delete to report false")
		}
	})

	t.Run("GetByID_NotFound", func(t *testing.T) {
		fetched, err := repo.GetByID(ctx, "non-existent-id")
		if err != nil {
			t.Fatalf("expected no error for not found, got %v", err)
		}
		if fetched != nil {
			t.Fatalf("expected nil for not found, got %+v", fetched)
		}
	})
}

func TestRedisSpotsRepo_Integration(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping integration test in short mode")
	}

	ctx := context.Background()

	// Start Redis via Testcontainers
	redisContainer, err := tcredis.Run(ctx, "redis:7-alpine")
	if err != nil {
		t.Fatalf("failed to start redis container: %v", err)
	}
	defer func() {
		_ = redisContainer.Terminate(ctx)
	}()

	uri, err := redisContainer.ConnectionString(ctx)
	if err != nil {
		t.Fatalf("failed to get redis connection string: %v", err)
	}

	opts, err := redis.ParseURL(uri)
	if err != nil {
		// Fallback for host:port
		endpoint, _ := redisContainer.PortEndpoint(ctx, "6379/tcp", "")
		opts = &redis.Options{Addr: endpoint}
	}

	rdb := redis.NewClient(opts)
	defer rdb.Close()

	repo := repository.NewRedisSpotsRepo(rdb)

	t.Run("GetAvailable_ReturnsCount", func(t *testing.T) {
		if err := repo.SetAvailable(ctx, 50); err != nil {
			t.Fatalf("failed to set available spots: %v", err)
		}
		spots, err := repo.GetAvailable(ctx)
		if err != nil {
			t.Fatalf("failed to get available spots: %v", err)
		}
		if spots != 50 {
			t.Fatalf("expected 50 spots, got %d", spots)
		}
	})

	t.Run("Increment", func(t *testing.T) {
		spots, err := repo.Increment(ctx)
		if err != nil {
			t.Fatalf("failed to increment spots: %v", err)
		}
		if spots != 51 {
			t.Fatalf("expected 51 spots, got %d", spots)
		}
	})

	t.Run("Decrement", func(t *testing.T) {
		spots, err := repo.Decrement(ctx)
		if err != nil {
			t.Fatalf("failed to decrement spots: %v", err)
		}
		if spots != 50 {
			t.Fatalf("expected 50 spots after decrement, got %d", spots)
		}
	})

	t.Run("Decrement_ClampsAtZero", func(t *testing.T) {
		if err := repo.SetAvailable(ctx, 0); err != nil {
			t.Fatalf("failed to set spots: %v", err)
		}
		spots, err := repo.Decrement(ctx)
		if err != nil || spots != 0 {
			t.Fatalf("expected 0, got %d, %v", spots, err)
		}
	})

	t.Run("Decrement_MissingKeyIsNotCreated", func(t *testing.T) {
		if err := rdb.Del(ctx, "spots:available").Err(); err != nil {
			t.Fatalf("failed to delete key: %v", err)
		}
		if _, err := repo.Decrement(ctx); !errors.Is(err, redis.Nil) {
			t.Fatalf("expected redis.Nil, got %v", err)
		}
		if n, _ := rdb.Exists(ctx, "spots:available").Result(); n != 0 {
			t.Fatalf("expected key to stay absent, got exists=%d", n)
		}
	})

	t.Run("Reserve_TakesASpot", func(t *testing.T) {
		if err := repo.SetAvailable(ctx, 2); err != nil {
			t.Fatalf("failed to set spots: %v", err)
		}
		reserved, err := repo.Reserve(ctx)
		if err != nil || !reserved {
			t.Fatalf("expected a spot reserved, got %v, %v", reserved, err)
		}
		if spots, _ := repo.GetAvailable(ctx); spots != 1 {
			t.Fatalf("expected 1 spot left, got %d", spots)
		}
	})

	t.Run("Reserve_LastSpotThenFull", func(t *testing.T) {
		// 1 spot left: the first car takes it, the second is refused and the
		// counter stays at 0.
		reserved, err := repo.Reserve(ctx)
		if err != nil || !reserved {
			t.Fatalf("expected the last spot reserved, got %v, %v", reserved, err)
		}
		reserved, err = repo.Reserve(ctx)
		if err != nil || reserved {
			t.Fatalf("expected a full lot to refuse, got %v, %v", reserved, err)
		}
		if spots, _ := repo.GetAvailable(ctx); spots != 0 {
			t.Fatalf("expected the counter to stay at 0, got %d", spots)
		}
	})

	t.Run("Reserve_MissingKeyIsNotCreated", func(t *testing.T) {
		if err := rdb.Del(ctx, "spots:available").Err(); err != nil {
			t.Fatalf("failed to delete key: %v", err)
		}
		if _, err := repo.Reserve(ctx); !errors.Is(err, redis.Nil) {
			t.Fatalf("expected redis.Nil, got %v", err)
		}
		if n, _ := rdb.Exists(ctx, "spots:available").Result(); n != 0 {
			t.Fatalf("expected key to stay absent, got exists=%d", n)
		}
	})

	t.Run("Increment_MissingKeyIsNotCreated", func(t *testing.T) {
		if err := rdb.Del(ctx, "spots:available").Err(); err != nil {
			t.Fatalf("failed to delete key: %v", err)
		}
		if _, err := repo.Increment(ctx); !errors.Is(err, redis.Nil) {
			t.Fatalf("expected redis.Nil, got %v", err)
		}
		if n, _ := rdb.Exists(ctx, "spots:available").Result(); n != 0 {
			t.Fatalf("expected key to stay absent, got exists=%d", n)
		}
	})
}
