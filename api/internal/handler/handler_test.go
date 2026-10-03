package handler_test

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"sort"
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

func (f *FakeSessionRepo) CountActive(ctx context.Context) (int64, error) {
	var count int64
	for _, s := range f.sessions {
		if s.Status == "PROCESSING" || s.Status == "PARKED" {
			count++
		}
	}
	return count, nil
}

func (f *FakeSessionRepo) ListByStatus(ctx context.Context, status string, limit int) ([]model.Session, error) {
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

// FakeAuditReader implements AuditReader for testing
type FakeAuditReader struct {
	events []model.AuditEvent
}

func (f *FakeAuditReader) ListEvents(ctx context.Context) ([]model.AuditEvent, error) {
	return f.events, nil
}

func setupTestMux(svc *service.ParkingService, audit *service.AuditService) *http.ServeMux {
	h := handler.NewHandler(svc, audit)
	mux := http.NewServeMux()
	h.RegisterRoutes(mux)
	return mux
}

func TestHealthCheck(t *testing.T) {
	mux := setupTestMux(nil, nil)
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
	mux := setupTestMux(svc, nil)

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
	mux := setupTestMux(svc, nil)

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
	mux := setupTestMux(svc, nil)

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
	mux := setupTestMux(svc, nil)

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
	mux := setupTestMux(svc, nil)

	req := httptest.NewRequest("POST", "/exits/unknown/pay", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Fatalf("expected status 404, got %d", rec.Code)
	}
}

func TestListSessions_Success(t *testing.T) {
	sessionRepo := NewFakeSessionRepo()
	now := time.Now().UTC()
	plate := "ABC1D23"
	_ = sessionRepo.Create(context.Background(), &model.Session{ID: "newer", LicensePlate: &plate, Status: "PARKED", S3PhotoKey: "photos/newer.jpg", EnteredAt: now})
	_ = sessionRepo.Create(context.Background(), &model.Session{ID: "older", Status: "PARKED", S3PhotoKey: "photos/older.jpg", EnteredAt: now.Add(-time.Hour)})
	_ = sessionRepo.Create(context.Background(), &model.Session{ID: "processing", Status: "PROCESSING", S3PhotoKey: "photos/p.jpg", EnteredAt: now})
	cfg := config.Config{TotalParkingSpots: 50, FixedParkingRate: 10.0}
	svc := service.NewParkingService(sessionRepo, nil, nil, nil, nil, cfg)
	mux := setupTestMux(svc, nil)

	req := httptest.NewRequest("GET", "/sessions", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d: %s", rec.Code, rec.Body.String())
	}
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Fatalf("expected Content-Type application/json, got %q", ct)
	}

	var res struct {
		Sessions []map[string]any `json:"sessions"`
	}
	if err := json.NewDecoder(rec.Body).Decode(&res); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	if len(res.Sessions) != 2 {
		t.Fatalf("expected 2 PARKED sessions, got %d", len(res.Sessions))
	}
	if res.Sessions[0]["id"] != "older" || res.Sessions[1]["id"] != "newer" {
		t.Fatalf("expected sessions ordered by entered_at asc, got %v", res.Sessions)
	}
	if res.Sessions[1]["license_plate"] != plate {
		t.Fatalf("expected license_plate %s, got %v", plate, res.Sessions[1]["license_plate"])
	}
	for _, s := range res.Sessions {
		if s["status"] != "PARKED" {
			t.Fatalf("expected status PARKED, got %v", s["status"])
		}
		if amount, ok := s["amount_due"].(float64); !ok || amount != 10.0 {
			t.Fatalf("expected amount_due 10.0, got %v", s["amount_due"])
		}
	}
}

func TestListSessions_StatusFilter(t *testing.T) {
	sessionRepo := NewFakeSessionRepo()
	_ = sessionRepo.Create(context.Background(), &model.Session{ID: "p1", Status: "PROCESSING", EnteredAt: time.Now()})
	_ = sessionRepo.Create(context.Background(), &model.Session{ID: "k1", Status: "PARKED", EnteredAt: time.Now()})
	svc := service.NewParkingService(sessionRepo, nil, nil, nil, nil, config.Config{FixedParkingRate: 10.0})
	mux := setupTestMux(svc, nil)

	req := httptest.NewRequest("GET", "/sessions?status=PROCESSING", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d: %s", rec.Code, rec.Body.String())
	}
	var res struct {
		Sessions []map[string]any `json:"sessions"`
	}
	if err := json.NewDecoder(rec.Body).Decode(&res); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	if len(res.Sessions) != 1 || res.Sessions[0]["id"] != "p1" {
		t.Fatalf("expected only session p1, got %v", res.Sessions)
	}
}

func TestListSessions_EmptyListIsArray(t *testing.T) {
	sessionRepo := NewFakeSessionRepo()
	svc := service.NewParkingService(sessionRepo, nil, nil, nil, nil, config.Config{FixedParkingRate: 10.0})
	mux := setupTestMux(svc, nil)

	req := httptest.NewRequest("GET", "/sessions", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d: %s", rec.Code, rec.Body.String())
	}
	if body := strings.TrimSpace(rec.Body.String()); body != `{"sessions":[]}` {
		t.Fatalf("expected empty sessions array, got %s", body)
	}
}

func TestListSessions_InvalidStatus(t *testing.T) {
	sessionRepo := NewFakeSessionRepo()
	svc := service.NewParkingService(sessionRepo, nil, nil, nil, nil, config.Config{FixedParkingRate: 10.0})
	mux := setupTestMux(svc, nil)

	req := httptest.NewRequest("GET", "/sessions?status=BOGUS", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d", rec.Code)
	}
	if body := strings.TrimSpace(rec.Body.String()); body != `{"error":"invalid status"}` {
		t.Fatalf("unexpected error body: %s", body)
	}
}

func TestGetSession_Success(t *testing.T) {
	sessionRepo := NewFakeSessionRepo()
	_ = sessionRepo.Create(context.Background(), &model.Session{ID: "abc", Status: "PARKED", S3PhotoKey: "photos/abc.jpg", EnteredAt: time.Now()})
	svc := service.NewParkingService(sessionRepo, nil, nil, nil, nil, config.Config{FixedParkingRate: 10.0})
	mux := setupTestMux(svc, nil)

	req := httptest.NewRequest("GET", "/sessions/abc", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d: %s", rec.Code, rec.Body.String())
	}
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Fatalf("expected Content-Type application/json, got %q", ct)
	}
	var res map[string]any
	if err := json.NewDecoder(rec.Body).Decode(&res); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	if res["id"] != "abc" || res["status"] != "PARKED" || res["s3_photo_key"] != "photos/abc.jpg" {
		t.Fatalf("unexpected session payload: %v", res)
	}
}

func TestGetSession_NotFound(t *testing.T) {
	sessionRepo := NewFakeSessionRepo()
	svc := service.NewParkingService(sessionRepo, nil, nil, nil, nil, config.Config{FixedParkingRate: 10.0})
	mux := setupTestMux(svc, nil)

	req := httptest.NewRequest("GET", "/sessions/unknown", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Fatalf("expected status 404, got %d", rec.Code)
	}
	if body := strings.TrimSpace(rec.Body.String()); body != `{"error":"session not found"}` {
		t.Fatalf("unexpected error body: %s", body)
	}
}

func TestListAuditEvents_Success(t *testing.T) {
	reader := &FakeAuditReader{events: []model.AuditEvent{
		{ID: "s1#1", Action: "ENTRY", EntityID: "s1", Timestamp: "2026-10-03T14:05:09Z", Details: map[string]any{"status": "PROCESSING"}},
		{ID: "s1#2", Action: "OCR_PROCESSING", EntityID: "s1", Timestamp: "2026-10-03T14:05:12.123456789Z", Details: map[string]any{"license_plate": "ABC1D23"}},
		{ID: "s1#3", Action: "EXIT_PAYMENT", EntityID: "s1", Timestamp: "2026-10-03T15:00:00Z", Details: map[string]any{"amount_paid": 10.0}},
	}}
	mux := setupTestMux(nil, service.NewAuditService(reader))

	req := httptest.NewRequest("GET", "/audit?limit=2", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d: %s", rec.Code, rec.Body.String())
	}
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Fatalf("expected Content-Type application/json, got %q", ct)
	}

	var res struct {
		Events []map[string]any `json:"events"`
	}
	if err := json.NewDecoder(rec.Body).Decode(&res); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	if len(res.Events) != 2 {
		t.Fatalf("expected 2 events, got %d", len(res.Events))
	}
	if res.Events[0]["action"] != "EXIT_PAYMENT" || res.Events[1]["action"] != "OCR_PROCESSING" {
		t.Fatalf("expected newest-first events, got %v", res.Events)
	}
	first := res.Events[0]
	if first["id"] != "s1#3" || first["entity_id"] != "s1" || first["timestamp"] != "2026-10-03T15:00:00Z" {
		t.Fatalf("unexpected event payload: %v", first)
	}
	details, ok := first["details"].(map[string]any)
	if !ok || details["amount_paid"] != 10.0 {
		t.Fatalf("expected details.amount_paid 10, got %v", first["details"])
	}
}

func TestListAuditEvents_DefaultLimit(t *testing.T) {
	var events []model.AuditEvent
	for i := 0; i < 150; i++ {
		events = append(events, model.AuditEvent{ID: fmt.Sprintf("e%d", i), Timestamp: time.Unix(int64(i), 0).UTC().Format(time.RFC3339Nano)})
	}
	mux := setupTestMux(nil, service.NewAuditService(&FakeAuditReader{events: events}))

	req := httptest.NewRequest("GET", "/audit", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d: %s", rec.Code, rec.Body.String())
	}
	var res struct {
		Events []map[string]any `json:"events"`
	}
	if err := json.NewDecoder(rec.Body).Decode(&res); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	if len(res.Events) != 100 {
		t.Fatalf("expected default limit of 100 events, got %d", len(res.Events))
	}
}

func TestListAuditEvents_EmptyListIsArray(t *testing.T) {
	mux := setupTestMux(nil, service.NewAuditService(&FakeAuditReader{}))

	req := httptest.NewRequest("GET", "/audit", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d: %s", rec.Code, rec.Body.String())
	}
	if body := strings.TrimSpace(rec.Body.String()); body != `{"events":[]}` {
		t.Fatalf("expected empty events array, got %s", body)
	}
}

func TestListAuditEvents_InvalidLimit(t *testing.T) {
	mux := setupTestMux(nil, service.NewAuditService(&FakeAuditReader{}))

	for _, q := range []string{"abc", "0", "-5", "501", "1.5", ""} {
		req := httptest.NewRequest("GET", "/audit?limit="+q, nil)
		rec := httptest.NewRecorder()

		mux.ServeHTTP(rec, req)

		if rec.Code != http.StatusBadRequest {
			t.Fatalf("limit=%q: expected status 400, got %d", q, rec.Code)
		}
		if body := strings.TrimSpace(rec.Body.String()); body != `{"error":"invalid limit"}` {
			t.Fatalf("limit=%q: unexpected error body: %s", q, body)
		}
	}
}
