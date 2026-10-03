package repository_test

import (
	"context"
	"database/sql"
	"testing"
	"time"

	"api/internal/config"
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

	// 2. Test Migrations (Up)
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
	})

	// 3. Test PostgresSessionRepo
	repo := repository.NewPostgresSessionRepo(db)

	t.Run("CreateAndGetByID", func(t *testing.T) {
		plate := "ABC1234"
		session := &model.Session{
			ID:           "test-pg-session-1",
			LicensePlate: &plate,
			Status:       "PROCESSING",
			S3PhotoKey:   "photos/car.jpg",
			EnteredAt:    time.Now().UTC().Truncate(time.Millisecond),
		}

		err := repo.Create(ctx, session)
		if err != nil {
			t.Fatalf("failed to create session: %v", err)
		}

		fetched, err := repo.GetByID(ctx, "test-pg-session-1")
		if err != nil {
			t.Fatalf("failed to get session by id: %v", err)
		}
		if fetched == nil {
			t.Fatal("expected session, got nil")
		}
		if fetched.ID != session.ID || fetched.Status != "PROCESSING" || *fetched.LicensePlate != plate {
			t.Fatalf("mismatched session data: %+v", fetched)
		}
	})

	t.Run("MarkAsPaid", func(t *testing.T) {
		exitedAt := time.Now().UTC().Truncate(time.Millisecond)
		amount := 12.50

		paid, err := repo.MarkAsPaid(ctx, "test-pg-session-1", exitedAt, amount)
		if err != nil {
			t.Fatalf("failed to mark session as paid: %v", err)
		}
		if paid.Status != "PAID" {
			t.Fatalf("expected status PAID, got %s", paid.Status)
		}
		if paid.AmountPaid == nil || *paid.AmountPaid != amount {
			t.Fatalf("expected amount %f, got %v", amount, paid.AmountPaid)
		}
	})

	t.Run("ListByStatus", func(t *testing.T) {
		base := time.Now().UTC().Truncate(time.Millisecond)
		fixtures := []model.Session{
			{ID: "list-parked-2", Status: "PARKED", S3PhotoKey: "photos/2.jpg", EnteredAt: base.Add(-1 * time.Hour)},
			{ID: "list-parked-1", Status: "PARKED", S3PhotoKey: "photos/1.jpg", EnteredAt: base.Add(-3 * time.Hour)},
			{ID: "list-parked-3", Status: "PARKED", S3PhotoKey: "photos/3.jpg", EnteredAt: base},
			{ID: "list-processing", Status: "PROCESSING", S3PhotoKey: "photos/p.jpg", EnteredAt: base.Add(-5 * time.Hour)},
		}
		for i := range fixtures {
			if err := repo.Create(ctx, &fixtures[i]); err != nil {
				t.Fatalf("failed to create session %s: %v", fixtures[i].ID, err)
			}
		}

		parked, err := repo.ListByStatus(ctx, "PARKED", 200)
		if err != nil {
			t.Fatalf("failed to list sessions: %v", err)
		}
		wantOrder := []string{"list-parked-1", "list-parked-2", "list-parked-3"}
		if len(parked) != len(wantOrder) {
			t.Fatalf("expected %d PARKED sessions, got %d: %+v", len(wantOrder), len(parked), parked)
		}
		for i, id := range wantOrder {
			if parked[i].ID != id {
				t.Fatalf("expected session %d to be %s, got %s", i, id, parked[i].ID)
			}
			if parked[i].Status != "PARKED" {
				t.Fatalf("expected status PARKED, got %s", parked[i].Status)
			}
		}

		limited, err := repo.ListByStatus(ctx, "PARKED", 2)
		if err != nil {
			t.Fatalf("failed to list sessions with limit: %v", err)
		}
		if len(limited) != 2 || limited[0].ID != "list-parked-1" || limited[1].ID != "list-parked-2" {
			t.Fatalf("expected first 2 PARKED sessions, got %+v", limited)
		}

		empty, err := repo.ListByStatus(ctx, "FAILED", 200)
		if err != nil {
			t.Fatalf("failed to list FAILED sessions: %v", err)
		}
		if empty == nil || len(empty) != 0 {
			t.Fatalf("expected non-nil empty slice, got %#v", empty)
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
}

func TestSQSEventPublisher_EmptyQueue(t *testing.T) {
	pub := repository.NewSQSEventPublisher(nil, config.Config{SQSQueueURL: ""})
	err := pub.Publish(context.Background(), map[string]string{"foo": "bar"})
	if err != nil {
		t.Fatalf("expected nil error when queue URL is empty, got %v", err)
	}
}
