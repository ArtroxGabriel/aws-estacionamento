package service_test

import (
	"bytes"
	"context"
	"errors"
	"io"
	"testing"
	"time"

	"api/internal/config"
	"api/internal/model"
	"api/internal/service"
)

// FakeSessionRepo implements repository.SessionRepository for testing
type FakeSessionRepo struct {
	sessions  map[string]*model.Session
	shouldErr bool
}

func (f *FakeSessionRepo) Create(ctx context.Context, s *model.Session) error {
	if f.shouldErr {
		return errors.New("db error")
	}
	f.sessions[s.ID] = s
	return nil
}

func (f *FakeSessionRepo) GetByID(ctx context.Context, id string) (*model.Session, error) {
	if f.shouldErr {
		return nil, errors.New("db error")
	}
	s, ok := f.sessions[id]
	if !ok {
		return nil, nil
	}
	return s, nil
}

func (f *FakeSessionRepo) MarkAsPaid(ctx context.Context, id string, exitedAt time.Time, amount float64) (*model.Session, error) {
	if f.shouldErr {
		return nil, errors.New("db error")
	}
	s, ok := f.sessions[id]
	if !ok {
		return nil, nil
	}
	s.Status = "PAID"
	s.ExitedAt = &exitedAt
	s.AmountPaid = &amount
	return s, nil
}

// FakeSpotsRepo implements repository.SpotsRepository
type FakeSpotsRepo struct {
	spots     int64
	shouldErr bool
}

func (f *FakeSpotsRepo) GetAvailable(ctx context.Context, defaultCap int) (int64, error) {
	if f.shouldErr {
		return 0, errors.New("redis error")
	}
	if f.spots == 0 {
		f.spots = int64(defaultCap)
	}
	return f.spots, nil
}

func (f *FakeSpotsRepo) Increment(ctx context.Context) (int64, error) {
	if f.shouldErr {
		return 0, errors.New("redis error")
	}
	f.spots++
	return f.spots, nil
}

func (f *FakeSpotsRepo) Decrement(ctx context.Context) (int64, error) {
	if f.shouldErr {
		return 0, errors.New("redis error")
	}
	f.spots--
	return f.spots, nil
}

// FakeStorage implements repository.BlobStorage
type FakeStorage struct {
	uploaded  map[string][]byte
	shouldErr bool
}

func (f *FakeStorage) Upload(ctx context.Context, key string, body io.Reader, contentType string) error {
	if f.shouldErr {
		return errors.New("s3 upload error")
	}
	data, _ := io.ReadAll(body)
	if f.uploaded == nil {
		f.uploaded = make(map[string][]byte)
	}
	f.uploaded[key] = data
	return nil
}

func (f *FakeStorage) Download(ctx context.Context, key string) (io.ReadCloser, string, error) {
	if f.shouldErr {
		return nil, "", errors.New("s3 download error")
	}
	data, ok := f.uploaded[key]
	if !ok {
		return nil, "", io.EOF
	}
	return io.NopCloser(bytes.NewReader(data)), "image/jpeg", nil
}

// FakePublisher implements repository.EventPublisher
type FakePublisher struct {
	published []any
}

func (f *FakePublisher) Publish(ctx context.Context, payload any) error {
	f.published = append(f.published, payload)
	return nil
}

// FakeAuditLogger implements repository.AuditLogger
type FakeAuditLogger struct {
	events []string
}

func (f *FakeAuditLogger) LogEvent(ctx context.Context, action, entityID string, details map[string]any) error {
	f.events = append(f.events, action)
	return nil
}

func setupService(shouldErrDB, shouldErrStorage bool) (*service.ParkingService, *FakeSessionRepo, *FakeSpotsRepo, *FakeStorage, *FakePublisher, *FakeAuditLogger) {
	repo := &FakeSessionRepo{sessions: make(map[string]*model.Session), shouldErr: shouldErrDB}
	spots := &FakeSpotsRepo{spots: 30}
	storage := &FakeStorage{shouldErr: shouldErrStorage}
	publisher := &FakePublisher{}
	audit := &FakeAuditLogger{}
	cfg := config.Config{TotalParkingSpots: 50, FixedParkingRate: 15.0}

	svc := service.NewParkingService(repo, spots, storage, publisher, audit, cfg)
	return svc, repo, spots, storage, publisher, audit
}

func TestGetAvailableSpots_Success(t *testing.T) {
	svc, _, spots, _, _, _ := setupService(false, false)

	available, err := svc.GetAvailableSpots(context.Background())
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
	if available != 30 {
		t.Fatalf("expected 30 spots, got %d", available)
	}
	_ = spots
}

func TestGetAvailableSpots_Error(t *testing.T) {
	repo := &FakeSessionRepo{sessions: make(map[string]*model.Session)}
	spots := &FakeSpotsRepo{shouldErr: true}
	cfg := config.Config{TotalParkingSpots: 50}
	svc := service.NewParkingService(repo, spots, nil, nil, nil, cfg)

	_, err := svc.GetAvailableSpots(context.Background())
	if err == nil {
		t.Fatal("expected error from redis, got nil")
	}
}

func TestCreateEntry_Success(t *testing.T) {
	svc, repo, _, storage, publisher, audit := setupService(false, false)

	body := bytes.NewReader([]byte("test-photo-data"))
	session, err := svc.CreateEntry(context.Background(), "car.jpg", body, "image/jpeg")

	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
	if session.ID == "" {
		t.Fatal("expected session ID to be populated")
	}
	if session.Status != "PROCESSING" {
		t.Fatalf("expected status PROCESSING, got %s", session.Status)
	}
	if len(repo.sessions) != 1 {
		t.Fatalf("expected 1 session saved, got %d", len(repo.sessions))
	}
	if len(storage.uploaded) != 1 {
		t.Fatalf("expected 1 photo uploaded, got %d", len(storage.uploaded))
	}
	if len(publisher.published) != 1 {
		t.Fatalf("expected 1 event published, got %d", len(publisher.published))
	}
	if len(audit.events) != 1 || audit.events[0] != "ENTRY" {
		t.Fatalf("expected 1 ENTRY audit event, got %v", audit.events)
	}
}

func TestCreateEntry_NilPhoto(t *testing.T) {
	svc, _, _, _, _, _ := setupService(false, false)

	_, err := svc.CreateEntry(context.Background(), "car.jpg", nil, "image/jpeg")
	if !errors.Is(err, service.ErrInvalidPhoto) {
		t.Fatalf("expected ErrInvalidPhoto, got %v", err)
	}
}

func TestCreateEntry_StorageFailure(t *testing.T) {
	svc, _, _, _, _, _ := setupService(false, true)

	body := bytes.NewReader([]byte("photo"))
	_, err := svc.CreateEntry(context.Background(), "car.jpg", body, "image/jpeg")
	if err == nil {
		t.Fatal("expected storage failure error, got nil")
	}
}

func TestCreateEntry_DatabaseFailure(t *testing.T) {
	svc, _, _, _, _, _ := setupService(true, false)

	body := bytes.NewReader([]byte("photo"))
	_, err := svc.CreateEntry(context.Background(), "car.jpg", body, "image/jpeg")
	if err == nil {
		t.Fatal("expected database failure error, got nil")
	}
}

func TestPayExit_Success(t *testing.T) {
	svc, repo, spots, _, _, audit := setupService(false, false)

	sessionID := "session-to-pay"
	repo.sessions[sessionID] = &model.Session{
		ID:         sessionID,
		Status:     "PARKED",
		S3PhotoKey: "photos/session-to-pay.jpg",
		EnteredAt:  time.Now().Add(-2 * time.Hour),
	}

	paid, err := svc.PayExit(context.Background(), sessionID)
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
	if paid.Status != "PAID" {
		t.Fatalf("expected status PAID, got %s", paid.Status)
	}
	if paid.AmountPaid == nil || *paid.AmountPaid != 15.0 {
		t.Fatalf("expected amount 15.0, got %v", paid.AmountPaid)
	}
	if spots.spots != 31 {
		t.Fatalf("expected spots incremented to 31, got %d", spots.spots)
	}
	if len(audit.events) != 1 || audit.events[0] != "EXIT_PAYMENT" {
		t.Fatalf("expected EXIT_PAYMENT audit event, got %v", audit.events)
	}
}

func TestPayExit_NotFound(t *testing.T) {
	svc, _, _, _, _, _ := setupService(false, false)

	_, err := svc.PayExit(context.Background(), "non-existent")
	if !errors.Is(err, service.ErrSessionNotFound) {
		t.Fatalf("expected ErrSessionNotFound, got %v", err)
	}
}

func TestPayExit_DatabaseError(t *testing.T) {
	svc, _, _, _, _, _ := setupService(true, false)

	_, err := svc.PayExit(context.Background(), "any-id")
	if err == nil {
		t.Fatal("expected database error, got nil")
	}
}
