package handler_test

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
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

func (f *FakeSessionRepo) UpdatePlate(ctx context.Context, id, plate string) (*model.Session, error) {
	s, ok := f.sessions[id]
	if !ok || (s.Status != "PARKED" && s.Status != "FAILED") {
		return nil, nil
	}
	s.LicensePlate = &plate
	s.Status = "PARKED"
	return s, nil
}

func (f *FakeSessionRepo) Delete(ctx context.Context, id string) (bool, error) {
	if _, ok := f.sessions[id]; !ok {
		return false, nil
	}
	delete(f.sessions, id)
	return true, nil
}

func (f *FakeSessionRepo) MarkAsFailed(ctx context.Context, id string) error {
	s, ok := f.sessions[id]
	if !ok {
		return nil
	}
	s.Status = "FAILED"
	return nil
}

func (f *FakeSessionRepo) FindAll(ctx context.Context, status *string, plate *string) ([]*model.Session, error) {
	var res []*model.Session
	for _, s := range f.sessions {
		if status != nil && *status != "" && s.Status != *status {
			continue
		}
		if plate != nil && *plate != "" && (s.LicensePlate == nil || *s.LicensePlate != *plate) {
			continue
		}
		res = append(res, s)
	}
	return res, nil
}

func (f *FakeSessionRepo) CountActive(ctx context.Context) (int64, error) {
	var count int64
	for _, s := range f.sessions {
		if s.Status == "PROCESSING" || s.Status == "PARKED" {
			count++
		}
	}
	return count, nil
}

// FakeSpotsRepo implements SpotsRepository for testing
type FakeSpotsRepo struct {
	spots int64
}

func (f *FakeSpotsRepo) GetAvailable(ctx context.Context) (int64, error) {
	return f.spots, nil
}

func (f *FakeSpotsRepo) SetAvailable(ctx context.Context, count int64) error {
	f.spots = count
	return nil
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

func (f *FakeStorage) Delete(ctx context.Context, key string) error {
	delete(f.uploaded, key)
	return nil
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

func (f *FakeAuditLogger) GetRecentLogs(ctx context.Context, limit int) ([]*model.AuditLog, error) {
	var logs []*model.AuditLog
	for _, e := range f.events {
		logs = append(logs, &model.AuditLog{
			Action:   e["action"].(string),
			EntityID: e["entity_id"].(string),
			Details:  e["details"].(map[string]any),
		})
	}
	return logs, nil
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
		t.Fatalf("expected event to be published to SQS")
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

func TestPayExit_InvalidStatus(t *testing.T) {
	sessionRepo := NewFakeSessionRepo()
	session := &model.Session{
		ID:         "test-session-processing",
		Status:     "PROCESSING",
		S3PhotoKey: "photos/test.jpg",
		EnteredAt:  time.Now().Add(-1 * time.Hour),
	}
	_ = sessionRepo.Create(context.Background(), session)

	cfg := config.Config{TotalParkingSpots: 50, FixedParkingRate: 10.0}
	svc := service.NewParkingService(sessionRepo, nil, nil, nil, nil, cfg)
	mux := setupTestMux(svc)

	req := httptest.NewRequest("POST", "/exits/test-session-processing/pay", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusConflict {
		t.Fatalf("expected status 409 Conflict, got %d: %s", rec.Code, rec.Body.String())
	}
}

func newPlateTestMux(status string) (*http.ServeMux, *FakeSessionRepo, *FakeSpotsRepo) {
	sessionRepo := NewFakeSessionRepo()
	spotsRepo := &FakeSpotsRepo{spots: 10}
	cfg := config.Config{TotalParkingSpots: 50, FixedParkingRate: 10.0}
	_ = sessionRepo.Create(context.Background(), &model.Session{
		ID: "s1", Status: status, S3PhotoKey: "photos/s1.jpg", EnteredAt: time.Now(),
	})
	svc := service.NewParkingService(sessionRepo, spotsRepo, &FakeStorage{}, nil, &FakeAuditLogger{}, cfg)
	return setupTestMux(svc), sessionRepo, spotsRepo
}

func TestUpdatePlate_Success(t *testing.T) {
	mux, _, spots := newPlateTestMux("FAILED")

	req := httptest.NewRequest("PATCH", "/sessions/s1", strings.NewReader(`{"license_plate":"abc-1d23"}`))
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	var res map[string]any
	_ = json.NewDecoder(rec.Body).Decode(&res)
	if res["license_plate"] != "ABC1D23" || res["status"] != "PARKED" {
		t.Fatalf("unexpected body: %v", res)
	}
	if spots.spots != 9 {
		t.Fatalf("expected spots 9, got %d", spots.spots)
	}
}

func TestUpdatePlate_Errors(t *testing.T) {
	cases := []struct {
		name, status, path, body string
		want                     int
	}{
		{"invalid plate", "FAILED", "/sessions/s1", `{"license_plate":"12"}`, http.StatusBadRequest},
		{"invalid json", "FAILED", "/sessions/s1", `{`, http.StatusBadRequest},
		{"not editable", "PAID", "/sessions/s1", `{"license_plate":"ABC1D23"}`, http.StatusConflict},
		{"not found", "FAILED", "/sessions/missing", `{"license_plate":"ABC1D23"}`, http.StatusNotFound},
	}
	for _, tc := range cases {
		mux, _, _ := newPlateTestMux(tc.status)
		req := httptest.NewRequest("PATCH", tc.path, strings.NewReader(tc.body))
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, req)
		if rec.Code != tc.want {
			t.Fatalf("%s: expected %d, got %d: %s", tc.name, tc.want, rec.Code, rec.Body.String())
		}
	}
}

func TestDeleteSession_Success(t *testing.T) {
	mux, repo, spots := newPlateTestMux("PARKED")

	req := httptest.NewRequest("DELETE", "/sessions/s1", nil)
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	if _, ok := repo.sessions["s1"]; ok {
		t.Fatal("expected session deleted")
	}
	if spots.spots != 11 {
		t.Fatalf("expected spots 11, got %d", spots.spots)
	}
}

func TestDeleteSession_NotFound(t *testing.T) {
	mux, _, _ := newPlateTestMux("PARKED")

	req := httptest.NewRequest("DELETE", "/sessions/missing", nil)
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", rec.Code)
	}
}

func TestCORS_AllowsPatchAndDelete(t *testing.T) {
	h := handler.WithCORS(http.NewServeMux())
	req := httptest.NewRequest("OPTIONS", "/sessions/s1", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	allowed := rec.Header().Get("Access-Control-Allow-Methods")
	if !strings.Contains(allowed, "PATCH") || !strings.Contains(allowed, "DELETE") {
		t.Fatalf("expected PATCH and DELETE in %q", allowed)
	}
}
