package handler_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/textproto"
	"strings"
	"testing"
	"time"

	"api/internal/config"
	"api/internal/handler"
	"api/internal/model"
	"api/internal/repository"
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

func (f *FakeSessionRepo) MarkAsPaid(ctx context.Context, id string, paidAt time.Time, amount float64) (*model.Session, error) {
	s, ok := f.sessions[id]
	if !ok || s.Status != model.StatusParked {
		return nil, nil
	}
	s.Status = model.StatusPaid
	s.PaidAt = &paidAt
	s.AmountPaid = &amount
	return s, nil
}

func (f *FakeSessionRepo) MarkAsExited(ctx context.Context, id string, exitedAt time.Time) (*model.Session, error) {
	s, ok := f.sessions[id]
	if !ok || s.Status != model.StatusPaid {
		return nil, nil
	}
	s.Status = model.StatusExited
	s.ExitedAt = &exitedAt
	return s, nil
}

func (f *FakeSessionRepo) FindOccupyingByPlate(ctx context.Context, plate string) ([]*model.Session, error) {
	var res []*model.Session
	for _, s := range f.sessions {
		if known, ok := s.KnownPlate(); ok && known == plate && s.Occupying() {
			res = append(res, s)
		}
	}
	return res, nil
}

func (f *FakeSessionRepo) UpdatePlate(ctx context.Context, id, plate string) (*model.Session, error) {
	s, ok := f.sessions[id]
	if !ok || !s.Occupying() {
		return nil, nil
	}
	s.LicensePlate = &plate
	s.PlateStatus = model.PlateManual
	return s, nil
}

func (f *FakeSessionRepo) Delete(ctx context.Context, id string) (bool, error) {
	if _, ok := f.sessions[id]; !ok {
		return false, nil
	}
	delete(f.sessions, id)
	return true, nil
}

func (f *FakeSessionRepo) MarkPlateUnreadable(ctx context.Context, id string) error {
	if s, ok := f.sessions[id]; ok && s.PlateStatus == model.PlatePending {
		s.PlateStatus = model.PlateUnreadable
	}
	return nil
}

func (f *FakeSessionRepo) FindAll(ctx context.Context, filter repository.SessionFilter) ([]*model.Session, error) {
	var res []*model.Session
	for _, s := range f.sessions {
		if filter.Status != "" && s.Status != filter.Status {
			continue
		}
		if filter.Plate != "" && (s.LicensePlate == nil || *s.LicensePlate != filter.Plate) {
			continue
		}
		if filter.PlateStatus != "" && s.PlateStatus != filter.PlateStatus {
			continue
		}
		res = append(res, s)
	}
	return res, nil
}

func (f *FakeSessionRepo) CountActive(ctx context.Context) (int64, error) {
	var count int64
	for _, s := range f.sessions {
		if s.Occupying() {
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

func (f *FakeSpotsRepo) Reserve(ctx context.Context) (bool, error) {
	if f.spots <= 0 {
		return false, nil
	}
	f.spots--
	return true, nil
}

// FakeStorage implements BlobStorage for testing
type FakeStorage struct {
	uploaded     map[string][]byte
	contentTypes map[string]string
}

func (f *FakeStorage) Upload(ctx context.Context, key string, body io.Reader, contentType string) error {
	data, _ := io.ReadAll(body)
	if f.uploaded == nil {
		f.uploaded = make(map[string][]byte)
		f.contentTypes = make(map[string]string)
	}
	f.uploaded[key] = data
	f.contentTypes[key] = contentType
	return nil
}

func (f *FakeStorage) Download(ctx context.Context, key string) (io.ReadCloser, string, error) {
	data, ok := f.uploaded[key]
	if !ok {
		return nil, "", repository.ErrBlobNotFound
	}
	return io.NopCloser(bytes.NewReader(data)), f.contentTypes[key], nil
}

func (f *FakeStorage) Delete(ctx context.Context, key string) error {
	delete(f.uploaded, key)
	return nil
}

// FakePublisher implements EventPublisher for testing
type FakePublisher struct {
	published []any
	shouldErr bool
}

func (f *FakePublisher) Publish(ctx context.Context, payload any) error {
	if f.shouldErr {
		return errors.New("queue is down")
	}
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

var testCfg = config.Config{TotalParkingSpots: 50, FixedParkingRate: 10.0}

// entryRequest builds the multipart POST /entries the totem sends.
func entryRequest(t *testing.T, contentType string) *http.Request {
	t.Helper()
	var b bytes.Buffer
	w := multipart.NewWriter(&b)
	header := textproto.MIMEHeader{}
	header.Set("Content-Disposition", `form-data; name="photo"; filename="car.jpg"`)
	header.Set("Content-Type", contentType)
	part, err := w.CreatePart(header)
	if err != nil {
		t.Fatalf("failed to create form file: %v", err)
	}
	_, _ = part.Write([]byte("fake-image-bytes"))
	_ = w.Close()

	req := httptest.NewRequest("POST", "/entries", &b)
	req.Header.Set("Content-Type", w.FormDataContentType())
	return req
}

func decode(t *testing.T, rec *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var res map[string]any
	if err := json.NewDecoder(rec.Body).Decode(&res); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	return res
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
	svc := service.NewParkingService(nil, spotsRepo, nil, nil, nil, testCfg)
	mux := setupTestMux(svc)

	req := httptest.NewRequest("GET", "/spots/available", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}

	res := decode(t, rec)
	if val, ok := res["available_spots"].(float64); !ok || int64(val) != 45 {
		t.Fatalf("expected available_spots to be 45, got %v", res["available_spots"])
	}
}

func TestCreateEntry_Success(t *testing.T) {
	sessionRepo := NewFakeSessionRepo()
	spotsRepo := &FakeSpotsRepo{spots: 10}
	storage := &FakeStorage{}
	publisher := &FakePublisher{}
	audit := &FakeAuditLogger{}

	svc := service.NewParkingService(sessionRepo, spotsRepo, storage, publisher, audit, testCfg)
	mux := setupTestMux(svc)

	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, entryRequest(t, "image/jpeg"))

	if rec.Code != http.StatusCreated {
		t.Fatalf("expected status 201, got %d: %s", rec.Code, rec.Body.String())
	}

	res := decode(t, rec)
	id, ok := res["id"].(string)
	if !ok || id == "" {
		t.Fatalf("expected valid id in response, got %v", res["id"])
	}
	// The ticket is valid at once: PARKED, plate still to be read.
	if res["status"] != "PARKED" {
		t.Fatalf("expected status PARKED, got %v", res["status"])
	}
	if res["plate_status"] != "PENDING" {
		t.Fatalf("expected plate_status PENDING, got %v", res["plate_status"])
	}
	if _, hasPlate := res["license_plate"]; hasPlate {
		t.Fatalf("expected no license_plate yet, got %v", res["license_plate"])
	}
	if spotsRepo.spots != 9 {
		t.Fatalf("expected the spot reserved (9), got %d", spotsRepo.spots)
	}
	if len(storage.uploaded) == 0 {
		t.Fatalf("expected file to be uploaded to storage")
	}
	if len(publisher.published) == 0 {
		t.Fatalf("expected event to be published to the queue")
	}
	if len(audit.events) == 0 {
		t.Fatalf("expected audit event to be logged")
	}
}

func TestCreateEntry_ParkingFull(t *testing.T) {
	sessionRepo := NewFakeSessionRepo()
	svc := service.NewParkingService(sessionRepo, &FakeSpotsRepo{spots: 0}, &FakeStorage{}, &FakePublisher{}, &FakeAuditLogger{}, testCfg)
	mux := setupTestMux(svc)

	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, entryRequest(t, "image/jpeg"))

	if rec.Code != http.StatusConflict {
		t.Fatalf("expected status 409 for a full lot, got %d: %s", rec.Code, rec.Body.String())
	}
	if res := decode(t, rec); res["error"] != "parking lot is full" {
		t.Fatalf("unexpected error body: %v", res)
	}
	if len(sessionRepo.sessions) != 0 {
		t.Fatalf("expected no session created, got %d", len(sessionRepo.sessions))
	}
}

// With the queue down the car still gets in: 201, valid ticket, plate unread.
func TestCreateEntry_QueueDownStillCreatesTheTicket(t *testing.T) {
	sessionRepo := NewFakeSessionRepo()
	svc := service.NewParkingService(sessionRepo, &FakeSpotsRepo{spots: 10}, &FakeStorage{}, &FakePublisher{shouldErr: true}, &FakeAuditLogger{}, testCfg)
	mux := setupTestMux(svc)

	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, entryRequest(t, "image/jpeg"))

	if rec.Code != http.StatusCreated {
		t.Fatalf("expected status 201, got %d: %s", rec.Code, rec.Body.String())
	}
	res := decode(t, rec)
	if res["status"] != "PARKED" || res["plate_status"] != "UNREADABLE" {
		t.Fatalf("expected PARKED/UNREADABLE, got %v/%v", res["status"], res["plate_status"])
	}
}

func TestCreateEntry_MissingPhoto(t *testing.T) {
	svc := service.NewParkingService(nil, nil, nil, nil, nil, testCfg)
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

	session := &model.Session{
		ID:          "test-session-123",
		Status:      model.StatusParked,
		PlateStatus: model.PlateRead,
		S3PhotoKey:  "photos/test-session-123.jpg",
		EnteredAt:   time.Now().Add(-1 * time.Hour),
	}
	_ = sessionRepo.Create(context.Background(), session)

	svc := service.NewParkingService(sessionRepo, spotsRepo, nil, nil, audit, testCfg)
	mux := setupTestMux(svc)

	req := httptest.NewRequest("POST", "/exits/test-session-123/pay", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d: %s", rec.Code, rec.Body.String())
	}

	res := decode(t, rec)
	if res["status"] != "PAID" {
		t.Fatalf("expected status PAID, got %v", res["status"])
	}

	// Paying keeps the spot: the car is still inside until the exit barrier.
	if spotsRepo.spots != 10 {
		t.Fatalf("expected spots unchanged (10), got %d", spotsRepo.spots)
	}

	if len(audit.events) == 0 {
		t.Fatalf("expected audit event to be logged")
	}
}

// A ticket whose photo was never read pays like any other.
func TestPayExit_UnreadablePlate(t *testing.T) {
	mux, _, spots := newSessionTestMux(model.StatusParked, model.PlateUnreadable)

	req := httptest.NewRequest("POST", "/exits/s1/pay", nil)
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d: %s", rec.Code, rec.Body.String())
	}
	res := decode(t, rec)
	if res["status"] != "PAID" || res["plate_status"] != "UNREADABLE" {
		t.Fatalf("expected PAID/UNREADABLE, got %v", res)
	}
	if spots.spots != 10 {
		t.Fatalf("expected spots unchanged (10), got %d", spots.spots)
	}
}

func TestPayExit_NotFound(t *testing.T) {
	sessionRepo := NewFakeSessionRepo()
	svc := service.NewParkingService(sessionRepo, nil, nil, nil, nil, testCfg)
	mux := setupTestMux(svc)

	req := httptest.NewRequest("POST", "/exits/unknown/pay", nil)
	rec := httptest.NewRecorder()

	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Fatalf("expected status 404, got %d", rec.Code)
	}
}

func TestPayExit_AlreadyPaid(t *testing.T) {
	mux, _, spots := newSessionTestMux(model.StatusPaid, model.PlateRead)

	req := httptest.NewRequest("POST", "/exits/s1/pay", nil)
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusConflict {
		t.Fatalf("expected status 409 Conflict, got %d: %s", rec.Code, rec.Body.String())
	}
	if spots.spots != 10 {
		t.Fatalf("expected spots untouched (10), got %d", spots.spots)
	}
}

// newSessionTestMux serves one session "s1" whose photo is in the storage.
func newSessionTestMux(status, plateStatus string) (*http.ServeMux, *FakeSessionRepo, *FakeSpotsRepo) {
	sessionRepo := NewFakeSessionRepo()
	spotsRepo := &FakeSpotsRepo{spots: 10}
	storage := &FakeStorage{}
	_ = storage.Upload(context.Background(), "photos/s1.jpg", strings.NewReader("jpeg-bytes"), "image/jpeg")
	_ = sessionRepo.Create(context.Background(), &model.Session{
		ID: "s1", Status: status, PlateStatus: plateStatus, S3PhotoKey: "photos/s1.jpg", EnteredAt: time.Now(),
	})
	svc := service.NewParkingService(sessionRepo, spotsRepo, storage, nil, &FakeAuditLogger{}, testCfg)
	return setupTestMux(svc), sessionRepo, spotsRepo
}

func TestUpdatePlate_Success(t *testing.T) {
	mux, _, spots := newSessionTestMux(model.StatusParked, model.PlateUnreadable)

	req := httptest.NewRequest("PATCH", "/sessions/s1", strings.NewReader(`{"license_plate":"abc-1d23"}`))
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	res := decode(t, rec)
	if res["license_plate"] != "ABC1D23" || res["status"] != "PARKED" || res["plate_status"] != "MANUAL" {
		t.Fatalf("unexpected body: %v", res)
	}
	// The spot was taken at the entry, not now.
	if spots.spots != 10 {
		t.Fatalf("expected spots unchanged (10), got %d", spots.spots)
	}
}

func TestUpdatePlate_Errors(t *testing.T) {
	cases := []struct {
		name, status, path, body string
		want                     int
	}{
		{"invalid plate", model.StatusParked, "/sessions/s1", `{"license_plate":"12"}`, http.StatusBadRequest},
		{"invalid json", model.StatusParked, "/sessions/s1", `{`, http.StatusBadRequest},
		{"paid is editable", model.StatusPaid, "/sessions/s1", `{"license_plate":"ABC1D23"}`, http.StatusOK},
		{"not editable", model.StatusExited, "/sessions/s1", `{"license_plate":"ABC1D23"}`, http.StatusConflict},
		{"not found", model.StatusParked, "/sessions/missing", `{"license_plate":"ABC1D23"}`, http.StatusNotFound},
	}
	for _, tc := range cases {
		mux, _, _ := newSessionTestMux(tc.status, model.PlateUnreadable)
		req := httptest.NewRequest("PATCH", tc.path, strings.NewReader(tc.body))
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, req)
		if rec.Code != tc.want {
			t.Fatalf("%s: expected %d, got %d: %s", tc.name, tc.want, rec.Code, rec.Body.String())
		}
	}
}

func TestDeleteSession_Success(t *testing.T) {
	mux, repo, spots := newSessionTestMux(model.StatusParked, model.PlatePending)

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
	mux, _, _ := newSessionTestMux(model.StatusParked, model.PlateRead)

	req := httptest.NewRequest("DELETE", "/sessions/missing", nil)
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", rec.Code)
	}
}

func TestGetSessionPhoto_Success(t *testing.T) {
	mux, _, _ := newSessionTestMux(model.StatusParked, model.PlateUnreadable)

	req := httptest.NewRequest("GET", "/sessions/s1/photo", nil)
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	if rec.Body.String() != "jpeg-bytes" {
		t.Fatalf("unexpected photo body %q", rec.Body.String())
	}
	if got := rec.Header().Get("Content-Type"); got != "image/jpeg" {
		t.Fatalf("expected image/jpeg, got %s", got)
	}
	if got := rec.Header().Get("X-Content-Type-Options"); got != "nosniff" {
		t.Fatalf("expected nosniff, got %q", got)
	}
	if got := rec.Header().Get("Content-Disposition"); got != "" {
		t.Fatalf("expected an inline image, got Content-Disposition %q", got)
	}
}

// The photo stays available as evidence after the car has left.
func TestGetSessionPhoto_PaidSession(t *testing.T) {
	mux, _, _ := newSessionTestMux(model.StatusPaid, model.PlateRead)

	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("GET", "/sessions/s1/photo", nil))

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
}

func TestGetSessionPhoto_UnsafeTypeIsADownload(t *testing.T) {
	sessionRepo := NewFakeSessionRepo()
	storage := &FakeStorage{}
	svc := service.NewParkingService(sessionRepo, &FakeSpotsRepo{spots: 10}, storage, &FakePublisher{}, &FakeAuditLogger{}, testCfg)
	mux := setupTestMux(svc)

	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, entryRequest(t, "text/html"))
	if rec.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d: %s", rec.Code, rec.Body.String())
	}
	id := decode(t, rec)["id"].(string)

	rec = httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("GET", "/sessions/"+id+"/photo", nil))

	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	if got := rec.Header().Get("Content-Type"); got != "application/octet-stream" {
		t.Fatalf("expected application/octet-stream, got %s", got)
	}
	if got := rec.Header().Get("Content-Disposition"); got != "attachment" {
		t.Fatalf("expected attachment, got %q", got)
	}
}

func TestGetSessionPhoto_NotFound(t *testing.T) {
	mux, repo, _ := newSessionTestMux(model.StatusParked, model.PlateRead)
	_ = repo.Create(context.Background(), &model.Session{ID: "nophoto", Status: model.StatusParked, S3PhotoKey: "photos/lost.jpg"})

	for _, id := range []string{"missing", "nophoto"} {
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, httptest.NewRequest("GET", "/sessions/"+id+"/photo", nil))
		if rec.Code != http.StatusNotFound {
			t.Fatalf("%s: expected 404, got %d", id, rec.Code)
		}
	}
}

func TestGetSessions_Filters(t *testing.T) {
	sessionRepo := NewFakeSessionRepo()
	plate := "ABC1D23"
	for _, s := range []*model.Session{
		{ID: "read", Status: model.StatusParked, PlateStatus: model.PlateRead, LicensePlate: &plate},
		{ID: "unread", Status: model.StatusParked, PlateStatus: model.PlateUnreadable},
		{ID: "paid", Status: model.StatusPaid, PlateStatus: model.PlateUnreadable},
	} {
		_ = sessionRepo.Create(context.Background(), s)
	}
	svc := service.NewParkingService(sessionRepo, nil, nil, nil, nil, testCfg)
	mux := setupTestMux(svc)

	cases := []struct {
		query string
		want  int
	}{
		{"", 2}, // defaults to PARKED
		{"?status=ALL", 3},
		{"?status=PAID", 1},
		{"?plate_status=UNREADABLE", 1}, // PARKED by default
		{"?status=ALL&plate_status=UNREADABLE", 2},
		{"?plate=ABC1D23", 1},
		{"?plate=ZZZ9Z99", 0},
	}
	for _, tc := range cases {
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, httptest.NewRequest("GET", "/sessions"+tc.query, nil))
		if rec.Code != http.StatusOK {
			t.Fatalf("%q: expected 200, got %d", tc.query, rec.Code)
		}
		var sessions []map[string]any
		if err := json.NewDecoder(rec.Body).Decode(&sessions); err != nil {
			t.Fatalf("%q: failed to decode: %v", tc.query, err)
		}
		if len(sessions) != tc.want {
			t.Fatalf("%q: expected %d sessions, got %d", tc.query, tc.want, len(sessions))
		}
		for _, s := range sessions {
			if _, ok := s["plate_status"]; !ok {
				t.Fatalf("%q: expected plate_status in every session, got %v", tc.query, s)
			}
		}
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

// ---------------------------------------------------------------------------
// Exit barrier
// ---------------------------------------------------------------------------

// newExitTestMux serves "s1" in the given status with plate ABC1D23 (READ).
func newExitTestMux(status string) (*http.ServeMux, *FakeSessionRepo, *FakeSpotsRepo) {
	mux, repo, spots := newSessionTestMux(status, model.PlateRead)
	plate := "ABC1D23"
	repo.sessions["s1"].LicensePlate = &plate
	return mux, repo, spots
}

func postJSON(mux *http.ServeMux, path, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest("POST", path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)
	return rec
}

func TestExitBarrier_OpensForAPaidCar(t *testing.T) {
	for path, body := range map[string]string{
		"/barrier/exit/ticket": `{"ticket_id": "s1"}`,
		"/barrier/exit/plate":  `{"plate": "abc-1d23"}`,
	} {
		mux, repo, spots := newExitTestMux(model.StatusPaid)
		rec := postJSON(mux, path, body)

		if rec.Code != http.StatusOK {
			t.Fatalf("%s: expected 200, got %d: %s", path, rec.Code, rec.Body.String())
		}
		res := decode(t, rec)
		if res["status"] != "EXITED" || res["exited_at"] == nil {
			t.Fatalf("%s: expected EXITED with exited_at, got %v", path, res)
		}
		if repo.sessions["s1"].Status != model.StatusExited || spots.spots != 11 {
			t.Fatalf("%s: expected EXITED and 11 spots, got %s and %d", path, repo.sessions["s1"].Status, spots.spots)
		}
	}
}

func TestExitBarrier_ErrorCodes(t *testing.T) {
	cases := []struct {
		name   string
		status string
		path   string
		body   string
		code   int
		errKey string
	}{
		{"ticket unpaid", model.StatusParked, "/barrier/exit/ticket", `{"ticket_id": "s1"}`, http.StatusConflict, "payment_pending"},
		{"ticket already left", model.StatusExited, "/barrier/exit/ticket", `{"ticket_id": "s1"}`, http.StatusConflict, "already_exited"},
		{"ticket unknown", model.StatusPaid, "/barrier/exit/ticket", `{"ticket_id": "nope"}`, http.StatusNotFound, "session_not_found"},
		{"ticket missing", model.StatusPaid, "/barrier/exit/ticket", `{}`, http.StatusBadRequest, "invalid_ticket"},
		{"ticket bad JSON", model.StatusPaid, "/barrier/exit/ticket", `{`, http.StatusBadRequest, "invalid_body"},
		{"plate unpaid", model.StatusParked, "/barrier/exit/plate", `{"plate": "ABC1D23"}`, http.StatusConflict, "payment_pending"},
		{"plate already left", model.StatusExited, "/barrier/exit/plate", `{"plate": "ABC1D23"}`, http.StatusNotFound, "plate_not_found"},
		{"plate unknown", model.StatusPaid, "/barrier/exit/plate", `{"plate": "LSN4I49"}`, http.StatusNotFound, "plate_not_found"},
		{"plate invalid", model.StatusPaid, "/barrier/exit/plate", `{"plate": "12"}`, http.StatusBadRequest, "invalid_plate"},
		{"plate bad JSON", model.StatusPaid, "/barrier/exit/plate", `nope`, http.StatusBadRequest, "invalid_body"},
	}
	for _, tc := range cases {
		mux, repo, spots := newExitTestMux(tc.status)
		rec := postJSON(mux, tc.path, tc.body)

		if rec.Code != tc.code {
			t.Fatalf("%s: expected %d, got %d: %s", tc.name, tc.code, rec.Code, rec.Body.String())
		}
		res := decode(t, rec)
		if res["code"] != tc.errKey || res["error"] == nil {
			t.Fatalf("%s: expected code %s with a message, got %v", tc.name, tc.errKey, res)
		}
		if repo.sessions["s1"].Status != tc.status || spots.spots != 10 {
			t.Fatalf("%s: expected nothing changed, got %s and %d spots", tc.name, repo.sessions["s1"].Status, spots.spots)
		}
	}
}

func TestGetSessions_FiltersExited(t *testing.T) {
	mux, _, _ := newExitTestMux(model.StatusExited)
	req := httptest.NewRequest("GET", "/sessions?status=EXITED", nil)
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)

	var sessions []map[string]any
	if err := json.NewDecoder(rec.Body).Decode(&sessions); err != nil || len(sessions) != 1 || sessions[0]["status"] != "EXITED" {
		t.Fatalf("expected the EXITED session, got %v, %v", sessions, err)
	}
}
