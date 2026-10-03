package service_test

import (
	"bytes"
	"context"
	"errors"
	"io"
	"sort"
	"testing"
	"time"

	"api/internal/config"
	"api/internal/model"
	"api/internal/service"
)

// FakeSessionRepo implements repository.SessionRepository for testing
type FakeSessionRepo struct {
	sessions   map[string]*model.Session
	shouldErr  bool
	lastStatus string
	lastLimit  int
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

func (f *FakeSessionRepo) CountActive(ctx context.Context) (int64, error) {
	if f.shouldErr {
		return 0, errors.New("db error")
	}
	var count int64
	for _, s := range f.sessions {
		if s.Status == "PROCESSING" || s.Status == "PARKED" {
			count++
		}
	}
	return count, nil
}

func (f *FakeSessionRepo) ListByStatus(ctx context.Context, status string, limit int) ([]model.Session, error) {
	f.lastStatus = status
	f.lastLimit = limit
	if f.shouldErr {
		return nil, errors.New("db error")
	}
	result := make([]model.Session, 0)
	for _, s := range f.sessions {
		if s.Status == status {
			result = append(result, *s)
		}
	}
	sort.Slice(result, func(i, j int) bool { return result[i].EnteredAt.Before(result[j].EnteredAt) })
	if len(result) > limit {
		result = result[:limit]
	}
	return result, nil
}

// FakeSpotsRepo implements repository.SpotsRepository
type FakeSpotsRepo struct {
	spots     int64
	shouldErr bool
}

func (f *FakeSpotsRepo) GetAvailable(ctx context.Context) (int64, error) {
	if f.shouldErr {
		return 0, errors.New("redis error")
	}
	if f.spots == 0 {
		return 0, errors.New("key missing in redis")
	}
	return f.spots, nil
}

func (f *FakeSpotsRepo) SetAvailable(ctx context.Context, count int64) error {
	if f.shouldErr {
		return errors.New("redis error")
	}
	f.spots = count
	return nil
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
	shouldErr bool
}

func (f *FakePublisher) Publish(ctx context.Context, payload any) error {
	if f.shouldErr {
		return errors.New("sqs publish error")
	}
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
	repo := &FakeSessionRepo{sessions: make(map[string]*model.Session), shouldErr: true}
	spots := &FakeSpotsRepo{shouldErr: true}
	cfg := config.Config{TotalParkingSpots: 50}
	svc := service.NewParkingService(repo, spots, nil, nil, nil, cfg)

	_, err := svc.GetAvailableSpots(context.Background())
	if err == nil {
		t.Fatal("expected error when both redis and db fail, got nil")
	}
}

func TestGetAvailableSpots_RedisRecovery(t *testing.T) {
	repo := &FakeSessionRepo{sessions: make(map[string]*model.Session)}
	// 12 active cars in parking lot
	for i := 0; i < 10; i++ {
		id := string(rune('a' + i))
		repo.sessions[id] = &model.Session{ID: id, Status: "PARKED"}
	}
	repo.sessions["proc1"] = &model.Session{ID: "proc1", Status: "PROCESSING"}
	repo.sessions["proc2"] = &model.Session{ID: "proc2", Status: "PROCESSING"}
	repo.sessions["paid1"] = &model.Session{ID: "paid1", Status: "PAID"} // should not count

	// Redis fell/rebooted (empty/missing key)
	spots := &FakeSpotsRepo{spots: 0}
	cfg := config.Config{TotalParkingSpots: 50}
	svc := service.NewParkingService(repo, spots, nil, nil, nil, cfg)

	// Redis returns error -> Service verifies DB -> 50 - 12 = 38 -> Updates Redis with 38
	available, err := svc.GetAvailableSpots(context.Background())
	if err != nil {
		t.Fatalf("expected successful recovery from DB, got %v", err)
	}
	if available != 38 {
		t.Fatalf("expected 38 recovered spots, got %d", available)
	}
	if spots.spots != 38 {
		t.Fatalf("expected redis to be updated with 38, got %d", spots.spots)
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

func TestCreateEntry_PublishFailure(t *testing.T) {
	svc, _, _, _, pub, _ := setupService(false, false)
	pub.shouldErr = true

	photoBody := bytes.NewReader([]byte("fake-photo"))
	session, err := svc.CreateEntry(context.Background(), "car.jpg", photoBody, "image/jpeg")
	if err == nil {
		t.Fatal("expected error on publish failure, got nil")
	}
	if session != nil {
		t.Fatalf("expected nil session on failure, got %+v", session)
	}
}

func TestPayExit_InvalidStatus(t *testing.T) {
	svc, repo, _, _, _, _ := setupService(false, false)

	sessionID := "processing-session"
	repo.sessions[sessionID] = &model.Session{
		ID:         sessionID,
		Status:     "PROCESSING",
		S3PhotoKey: "photos/processing.jpg",
		EnteredAt:  time.Now().Add(-1 * time.Hour),
	}

	_, err := svc.PayExit(context.Background(), sessionID)
	if err == nil {
		t.Fatal("expected error when paying session in PROCESSING status, got nil")
	}
	if !errors.Is(err, service.ErrInvalidSessionStatus) {
		t.Fatalf("expected ErrInvalidSessionStatus, got %v", err)
	}

	// Test with already PAID session
	paidID := "paid-session"
	repo.sessions[paidID] = &model.Session{
		ID:         paidID,
		Status:     "PAID",
		S3PhotoKey: "photos/paid.jpg",
		EnteredAt:  time.Now().Add(-2 * time.Hour),
	}

	_, err = svc.PayExit(context.Background(), paidID)
	if err == nil {
		t.Fatal("expected error when paying session already in PAID status, got nil")
	}
	if !errors.Is(err, service.ErrInvalidSessionStatus) {
		t.Fatalf("expected ErrInvalidSessionStatus, got %v", err)
	}
}

func TestListSessions_DefaultsToParkedWithAmountDue(t *testing.T) {
	svc, repo, _, _, _, _ := setupService(false, false)
	now := time.Now()
	repo.sessions["b"] = &model.Session{ID: "b", Status: "PARKED", EnteredAt: now}
	repo.sessions["a"] = &model.Session{ID: "a", Status: "PARKED", EnteredAt: now.Add(-time.Hour)}
	repo.sessions["p"] = &model.Session{ID: "p", Status: "PROCESSING", EnteredAt: now}

	sessions, err := svc.ListSessions(context.Background(), "")
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
	if repo.lastStatus != "PARKED" {
		t.Fatalf("expected default status PARKED, got %q", repo.lastStatus)
	}
	if repo.lastLimit != 200 {
		t.Fatalf("expected limit 200, got %d", repo.lastLimit)
	}
	if len(sessions) != 2 || sessions[0].ID != "a" || sessions[1].ID != "b" {
		t.Fatalf("expected sessions [a b], got %+v", sessions)
	}
	for _, s := range sessions {
		if s.AmountDue != 15.0 {
			t.Fatalf("expected amount_due 15.0, got %v", s.AmountDue)
		}
	}
}

func TestListSessions_AcceptsValidStatuses(t *testing.T) {
	for _, status := range []string{"PROCESSING", "PARKED", "PAID", "FAILED"} {
		svc, repo, _, _, _, _ := setupService(false, false)
		sessions, err := svc.ListSessions(context.Background(), status)
		if err != nil {
			t.Fatalf("status %s: expected no error, got %v", status, err)
		}
		if repo.lastStatus != status {
			t.Fatalf("status %s: repository received %q", status, repo.lastStatus)
		}
		if sessions == nil {
			t.Fatalf("status %s: expected non-nil empty slice", status)
		}
	}
}

func TestListSessions_InvalidStatus(t *testing.T) {
	svc, repo, _, _, _, _ := setupService(false, false)

	for _, status := range []string{"BOGUS", "parked", "EXITED"} {
		_, err := svc.ListSessions(context.Background(), status)
		if !errors.Is(err, service.ErrInvalidStatusFilter) {
			t.Fatalf("status %q: expected ErrInvalidStatusFilter, got %v", status, err)
		}
	}
	if repo.lastStatus != "" {
		t.Fatalf("expected repository not to be called, got status %q", repo.lastStatus)
	}
}

func TestListSessions_DatabaseError(t *testing.T) {
	svc, _, _, _, _, _ := setupService(true, false)

	_, err := svc.ListSessions(context.Background(), "PARKED")
	if err == nil || errors.Is(err, service.ErrInvalidStatusFilter) {
		t.Fatalf("expected database error, got %v", err)
	}
}

func TestGetSession_Success(t *testing.T) {
	svc, repo, _, _, _, _ := setupService(false, false)
	repo.sessions["abc"] = &model.Session{ID: "abc", Status: "PARKED"}

	session, err := svc.GetSession(context.Background(), "abc")
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
	if session.ID != "abc" {
		t.Fatalf("expected session abc, got %+v", session)
	}
}

func TestGetSession_NotFound(t *testing.T) {
	svc, _, _, _, _, _ := setupService(false, false)

	_, err := svc.GetSession(context.Background(), "missing")
	if !errors.Is(err, service.ErrSessionNotFound) {
		t.Fatalf("expected ErrSessionNotFound, got %v", err)
	}
}

func TestGetSession_DatabaseError(t *testing.T) {
	svc, _, _, _, _, _ := setupService(true, false)

	_, err := svc.GetSession(context.Background(), "any")
	if err == nil || errors.Is(err, service.ErrSessionNotFound) {
		t.Fatalf("expected database error, got %v", err)
	}
}
