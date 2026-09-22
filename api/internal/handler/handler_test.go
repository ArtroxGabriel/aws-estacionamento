package handler_test

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"api/internal/config"
	"api/internal/handler"
	"api/internal/model"
	"api/internal/service"
)

// FakeSessionRepo implements SessionRepository for testing
type FakeSessionRepo struct {
	sessions map[string]*model.Session
}

func NewFakeSessionRepo() *FakeSessionRepo {
	return &FakeSessionRepo{sessions: make(map[string]*model.Session)}
}

func (f *FakeSessionRepo) Create(ctx context.Context, s *model.Session) error {
	f.sessions[s.ID] = s
	return nil
}

func (f *FakeSessionRepo) GetByID(ctx context.Context, id string) (*model.Session, error) {
	s, ok := f.sessions[id]
	if !ok {
		return nil, nil
	}
	return s, nil
}

func (f *FakeSessionRepo) MarkAsPaid(ctx context.Context, id string, exitedAt time.Time, amount float64) (*model.Session, error) {
	s, ok := f.sessions[id]
	if !ok {
		return nil, nil
	}
	s.Status = "PAID"
	s.ExitedAt = &exitedAt
	s.AmountPaid = &amount
	return s, nil
}

// FakeSpotsRepo implements SpotsRepository for testing
type FakeSpotsRepo struct {
	spots int64
}

func (f *FakeSpotsRepo) GetAvailable(ctx context.Context, defaultCap int) (int64, error) {
	if f.spots == 0 {
		f.spots = int64(defaultCap)
	}
	return f.spots, nil
}

func (f *FakeSpotsRepo) Increment(ctx context.Context) (int64, error) {
	f.spots++
	return f.spots, nil
}

func (f *FakeSpotsRepo) Decrement(ctx context.Context) (int64, error) {
	f.spots--
	return f.spots, nil
}

// FakeStorage implements BlobStorage for testing
type FakeStorage struct {
	uploaded map[string][]byte
}

func (f *FakeStorage) Upload(ctx context.Context, key string, body io.Reader, contentType string) error {
	data, _ := io.ReadAll(body)
	if f.uploaded == nil {
		f.uploaded = make(map[string][]byte)
	}
	f.uploaded[key] = data
	return nil
}

func (f *FakeStorage) Download(ctx context.Context, key string) (io.ReadCloser, string, error) {
	data, ok := f.uploaded[key]
	if !ok {
		return nil, "", io.EOF
	}
	return io.NopCloser(bytes.NewReader(data)), "image/jpeg", nil
}

// FakePublisher implements EventPublisher for testing
type FakePublisher struct {
	published []any
}

func (f *FakePublisher) Publish(ctx context.Context, payload any) error {
	f.published = append(f.published, payload)
	return nil
}

// FakeAuditLogger implements AuditLogger for testing
type FakeAuditLogger struct {
	events []map[string]any
}

func (f *FakeAuditLogger) LogEvent(ctx context.Context, action, entityID string, details map[string]any) error {
	f.events = append(f.events, map[string]any{
		"action":    action,
		"entity_id": entityID,
		"details":   details,
	})
	return nil
}

func setupTestMux(svc *service.ParkingService) *http.ServeMux {
	h := handler.NewHandler(svc)
	mux := http.NewServeMux()
	h.RegisterRoutes(mux)
	return mux
}

func TestHealthCheck(t *testing.T) {
	mux := setupTestMux(nil)
	req := httptest.NewRequest("GET", "/health", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}
}

func TestGetAvailableSpots(t *testing.T) {
	spotsRepo := &FakeSpotsRepo{spots: 45}
	cfg := config.Config{TotalParkingSpots: 50, FixedParkingRate: 10.0}
	svc := service.NewParkingService(nil, spotsRepo, nil, nil, nil, cfg)
	mux := setupTestMux(svc)

	req := httptest.NewRequest("GET", "/spots/available", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}

	var res map[string]any
	if err := json.NewDecoder(rec.Body).Decode(&res); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if val, ok := res["available_spots"].(float64); !ok || int64(val) != 45 {
		t.Fatalf("expected available_spots to be 45, got %v", res["available_spots"])
	}
}

func TestCreateEntry_Success(t *testing.T) {
	sessionRepo := NewFakeSessionRepo()
	storage := &FakeStorage{}
	publisher := &FakePublisher{}
	audit := &FakeAuditLogger{}
	cfg := config.Config{TotalParkingSpots: 50, FixedParkingRate: 10.0}

	svc := service.NewParkingService(sessionRepo, nil, storage, publisher, audit, cfg)
	mux := setupTestMux(svc)

	var b bytes.Buffer
	w := multipart.NewWriter(&b)
	part, err := w.CreateFormFile("photo", "car.jpg")
	if err != nil {
		t.Fatalf("failed to create form file: %v", err)
	}
	part.Write([]byte("fake-image-bytes"))
	w.Close()

	req := httptest.NewRequest("POST", "/entries", &b)
	req.Header.Set("Content-Type", w.FormDataContentType())
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusCreated {
		t.Fatalf("expected status 201, got %d: %s", rec.Code, rec.Body.String())
	}

	var res map[string]any
	if err := json.NewDecoder(rec.Body).Decode(&res); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	id, ok := res["id"].(string)
	if !ok || id == "" {
		t.Fatalf("expected valid id in response, got %v", res["id"])
	}

	if res["status"] != "PROCESSING" {
		t.Fatalf("expected status PROCESSING, got %v", res["status"])
	}

	if len(storage.uploaded) == 0 {
		t.Fatalf("expected file to be uploaded to storage")
	}

	if len(publisher.published) == 0 {
		t.Fatalf("expected event to be published to SNS")
	}

	if len(audit.events) == 0 {
		t.Fatalf("expected audit event to be logged")
	}
}

func TestCreateEntry_MissingPhoto(t *testing.T) {
	cfg := config.Config{TotalParkingSpots: 50, FixedParkingRate: 10.0}
	svc := service.NewParkingService(nil, nil, nil, nil, nil, cfg)
	mux := setupTestMux(svc)

	req := httptest.NewRequest("POST", "/entries", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400 for missing photo, got %d", rec.Code)
	}
}

func TestPayExit_Success(t *testing.T) {
	sessionRepo := NewFakeSessionRepo()
	spotsRepo := &FakeSpotsRepo{spots: 10}
	audit := &FakeAuditLogger{}
	cfg := config.Config{TotalParkingSpots: 50, FixedParkingRate: 10.0}

	session := &model.Session{
		ID:         "test-session-123",
		Status:     "PARKED",
		S3PhotoKey: "photos/test-session-123.jpg",
		EnteredAt:  time.Now().Add(-1 * time.Hour),
	}
	_ = sessionRepo.Create(context.Background(), session)

	svc := service.NewParkingService(sessionRepo, spotsRepo, nil, nil, audit, cfg)
	mux := setupTestMux(svc)

	req := httptest.NewRequest("POST", "/exits/test-session-123/pay", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d: %s", rec.Code, rec.Body.String())
	}

	var res map[string]any
	if err := json.NewDecoder(rec.Body).Decode(&res); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if res["status"] != "PAID" {
		t.Fatalf("expected status PAID, got %v", res["status"])
	}

	if spotsRepo.spots != 11 {
		t.Fatalf("expected spots to increment to 11, got %d", spotsRepo.spots)
	}

	if len(audit.events) == 0 {
		t.Fatalf("expected audit event to be logged")
	}
}

func TestPayExit_NotFound(t *testing.T) {
	sessionRepo := NewFakeSessionRepo()
	cfg := config.Config{TotalParkingSpots: 50, FixedParkingRate: 10.0}
	svc := service.NewParkingService(sessionRepo, nil, nil, nil, nil, cfg)
	mux := setupTestMux(svc)

	req := httptest.NewRequest("POST", "/exits/unknown/pay", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Fatalf("expected status 404, got %d", rec.Code)
	}
}
