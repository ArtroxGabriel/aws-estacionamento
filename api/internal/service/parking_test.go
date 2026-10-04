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
	if s.Status != "PARKED" && s.Status != "FAILED" {
		return nil, nil
	}
	s.Status = "PAID"
	s.ExitedAt = &exitedAt
	s.AmountPaid = &amount
	return s, nil
}

func (f *FakeSessionRepo) MarkAsFailed(ctx context.Context, id string) error {
	if f.shouldErr {
		return errors.New("db error")
	}
	s, ok := f.sessions[id]
	if !ok {
		return nil
	}
	s.Status = "FAILED"
	return nil
}

func (f *FakeSessionRepo) FindAll(ctx context.Context, status *string, plate *string) ([]*model.Session, error) {
	if f.shouldErr {
		return nil, errors.New("db error")
	}
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

func (f *FakeSessionRepo) UpdatePlate(ctx context.Context, id, plate string) (*model.Session, error) {
	if f.shouldErr {
		return nil, errors.New("db error")
	}
	s, ok := f.sessions[id]
	if !ok || (s.Status != "PARKED" && s.Status != "FAILED") {
		return nil, nil
	}
	s.LicensePlate = &plate
	s.Status = "PARKED"
	return s, nil
}

func (f *FakeSessionRepo) Delete(ctx context.Context, id string) (bool, error) {
	if f.shouldErr {
		return false, errors.New("db error")
	}
	if _, ok := f.sessions[id]; !ok {
		return false, nil
	}
	delete(f.sessions, id)
	return true, nil
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
	deleted   []string
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

func (f *FakeStorage) Delete(ctx context.Context, key string) error {
	if f.shouldErr {
		return errors.New("s3 delete error")
	}
	f.deleted = append(f.deleted, key)
	delete(f.uploaded, key)
	return nil
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

func (f *FakeAuditLogger) GetRecentLogs(ctx context.Context, limit int) ([]*model.AuditLog, error) {
	var logs []*model.AuditLog
	for _, a := range f.events {
		logs = append(logs, &model.AuditLog{
			Action: a,
		})
	}
	return logs, nil
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
	svc, repo, _, _, pub, audit := setupService(false, false)
	pub.shouldErr = true

	photoBody := bytes.NewReader([]byte("fake-photo"))
	session, err := svc.CreateEntry(context.Background(), "car.jpg", photoBody, "image/jpeg")
	if err == nil {
		t.Fatal("expected error on publish failure, got nil")
	}
	if session != nil {
		t.Fatalf("expected nil session on failure, got %+v", session)
	}

	if len(repo.sessions) != 1 {
		t.Fatalf("expected 1 session in repo, got %d", len(repo.sessions))
	}
	for _, s := range repo.sessions {
		if s.Status != "FAILED" {
			t.Fatalf("expected session status to be FAILED, got %s", s.Status)
		}
	}

	foundAudit := false
	for _, a := range audit.events {
		if a == "ENTRY_FAILED" {
			foundAudit = true
			break
		}
	}
	if !foundAudit {
		t.Fatal("expected ENTRY_FAILED audit event")
	}
}

func TestPayExit_FailedStatus_Success(t *testing.T) {
	svc, repo, spots, _, _, audit := setupService(false, false)

	sessionID := "failed-ocr-session"
	repo.sessions[sessionID] = &model.Session{
		ID:         sessionID,
		Status:     "FAILED",
		S3PhotoKey: "photos/blurry.jpg",
		EnteredAt:  time.Now().Add(-1 * time.Hour),
	}

	initialSpots := spots.spots
	paid, err := svc.PayExit(context.Background(), sessionID)
	if err != nil {
		t.Fatalf("expected success paying FAILED session, got %v", err)
	}
	if paid.Status != "PAID" {
		t.Fatalf("expected status PAID, got %s", paid.Status)
	}
	// For FAILED sessions, spots counter must NOT increment because it was never decremented
	if spots.spots != initialSpots {
		t.Fatalf("expected spots counter unchanged (%d), got %d", initialSpots, spots.spots)
	}

	foundAudit := false
	for _, a := range audit.events {
		if a == "EXIT_PAYMENT" {
			foundAudit = true
			break
		}
	}
	if !foundAudit {
		t.Fatal("expected EXIT_PAYMENT audit event")
	}
}

func TestPayExit_ParkedStatus_Success(t *testing.T) {
	svc, repo, spots, _, _, audit := setupService(false, false)

	sessionID := "parked-session"
	plate := "ABC1D23"
	repo.sessions[sessionID] = &model.Session{
		ID:           sessionID,
		LicensePlate: &plate,
		Status:       "PARKED",
		S3PhotoKey:   "photos/valid.jpg",
		EnteredAt:    time.Now().Add(-1 * time.Hour),
	}

	initialSpots := spots.spots
	paid, err := svc.PayExit(context.Background(), sessionID)
	if err != nil {
		t.Fatalf("expected success paying PARKED session, got %v", err)
	}
	if paid.Status != "PAID" {
		t.Fatalf("expected status PAID, got %s", paid.Status)
	}
	// For PARKED sessions, spots counter must increment
	if spots.spots != initialSpots+1 {
		t.Fatalf("expected spots counter incremented to %d, got %d", initialSpots+1, spots.spots)
	}

	foundAudit := false
	for _, a := range audit.events {
		if a == "EXIT_PAYMENT" {
			foundAudit = true
			break
		}
	}
	if !foundAudit {
		t.Fatal("expected EXIT_PAYMENT audit event")
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


func hasEvent(audit *FakeAuditLogger, action string) bool {
	for _, a := range audit.events {
		if a == action {
			return true
		}
	}
	return false
}

func TestUpdatePlate_FailedSessionBecomesParkedAndTakesASpot(t *testing.T) {
	svc, repo, spots, _, _, audit := setupService(false, false)
	repo.sessions["s1"] = &model.Session{ID: "s1", Status: "FAILED", EnteredAt: time.Now()}
	before := spots.spots

	got, err := svc.UpdatePlate(context.Background(), "s1", " abc-1d23 ")
	if err != nil {
		t.Fatalf("expected success, got %v", err)
	}
	if got.Status != "PARKED" || got.LicensePlate == nil || *got.LicensePlate != "ABC1D23" {
		t.Fatalf("expected PARKED with ABC1D23, got %s %v", got.Status, got.LicensePlate)
	}
	// The FAILED session never took a spot; now that it is PARKED it does.
	if spots.spots != before-1 {
		t.Fatalf("expected spots %d, got %d", before-1, spots.spots)
	}
	if !hasEvent(audit, "PLATE_CORRECTION") {
		t.Fatal("expected PLATE_CORRECTION audit event")
	}
}

func TestUpdatePlate_ParkedSessionKeepsTheCounter(t *testing.T) {
	svc, repo, spots, _, _, _ := setupService(false, false)
	old := "ABC1234"
	repo.sessions["s1"] = &model.Session{ID: "s1", Status: "PARKED", LicensePlate: &old}
	before := spots.spots

	got, err := svc.UpdatePlate(context.Background(), "s1", "LSN4I49")
	if err != nil {
		t.Fatalf("expected success, got %v", err)
	}
	if *got.LicensePlate != "LSN4I49" || spots.spots != before {
		t.Fatalf("expected LSN4I49 and spots %d, got %s and %d", before, *got.LicensePlate, spots.spots)
	}
}

func TestUpdatePlate_RejectsInvalidPlate(t *testing.T) {
	svc, repo, _, _, _, _ := setupService(false, false)
	repo.sessions["s1"] = &model.Session{ID: "s1", Status: "FAILED"}

	for _, plate := range []string{"", "AB12345", "1234567", "ABC12345", "AB1234", "ABCDE12"} {
		if _, err := svc.UpdatePlate(context.Background(), "s1", plate); !errors.Is(err, service.ErrInvalidPlate) {
			t.Fatalf("plate %q: expected ErrInvalidPlate, got %v", plate, err)
		}
	}
}

func TestUpdatePlate_OnlyParkedOrFailed(t *testing.T) {
	svc, repo, _, _, _, _ := setupService(false, false)
	repo.sessions["proc"] = &model.Session{ID: "proc", Status: "PROCESSING"}
	repo.sessions["paid"] = &model.Session{ID: "paid", Status: "PAID"}

	for _, id := range []string{"proc", "paid"} {
		if _, err := svc.UpdatePlate(context.Background(), id, "ABC1D23"); !errors.Is(err, service.ErrSessionNotEditable) {
			t.Fatalf("%s: expected ErrSessionNotEditable, got %v", id, err)
		}
	}
	if _, err := svc.UpdatePlate(context.Background(), "missing", "ABC1D23"); !errors.Is(err, service.ErrSessionNotFound) {
		t.Fatalf("expected ErrSessionNotFound, got %v", err)
	}
}

func TestDeleteSession_ParkedFreesTheSpotAndRemovesThePhoto(t *testing.T) {
	svc, repo, spots, storage, _, audit := setupService(false, false)
	repo.sessions["s1"] = &model.Session{ID: "s1", Status: "PARKED", S3PhotoKey: "photos/s1_car.jpg"}
	before := spots.spots

	deleted, err := svc.DeleteSession(context.Background(), "s1")
	if err != nil {
		t.Fatalf("expected success, got %v", err)
	}
	if deleted.ID != "s1" {
		t.Fatalf("expected deleted session s1, got %s", deleted.ID)
	}
	if _, ok := repo.sessions["s1"]; ok {
		t.Fatal("expected session removed from the repository")
	}
	if spots.spots != before+1 {
		t.Fatalf("expected spots %d, got %d", before+1, spots.spots)
	}
	if len(storage.deleted) != 1 || storage.deleted[0] != "photos/s1_car.jpg" {
		t.Fatalf("expected photo deleted, got %v", storage.deleted)
	}
	if !hasEvent(audit, "SESSION_DELETE") {
		t.Fatal("expected SESSION_DELETE audit event")
	}
}

func TestDeleteSession_NonParkedKeepsTheCounter(t *testing.T) {
	for _, status := range []string{"PROCESSING", "FAILED", "PAID"} {
		svc, repo, spots, _, _, _ := setupService(false, false)
		repo.sessions["s1"] = &model.Session{ID: "s1", Status: status}
		before := spots.spots

		if _, err := svc.DeleteSession(context.Background(), "s1"); err != nil {
			t.Fatalf("%s: expected success, got %v", status, err)
		}
		if spots.spots != before {
			t.Fatalf("%s: expected spots unchanged (%d), got %d", status, before, spots.spots)
		}
	}
}

func TestDeleteSession_NotFound(t *testing.T) {
	svc, _, _, _, _, _ := setupService(false, false)

	if _, err := svc.DeleteSession(context.Background(), "missing"); !errors.Is(err, service.ErrSessionNotFound) {
		t.Fatalf("expected ErrSessionNotFound, got %v", err)
	}
}

func TestUpdatePlate_AcceptsMercosulCountries(t *testing.T) {
	for plate, want := range map[string]string{
		"abc-1d23": "ABC1D23", // Brasil (Mercosul)
		"ABC 1234": "ABC1234", // Brasil (antiga) / Uruguai
		"AA 562 AN": "AA562AN", // Argentina (Mercosul)
		"MWV 724":   "MWV724",  // Argentina (antiga)
		"ABCD 123":  "ABCD123", // Paraguai
	} {
		svc, repo, _, _, _, _ := setupService(false, false)
		repo.sessions["s1"] = &model.Session{ID: "s1", Status: "FAILED"}
		got, err := svc.UpdatePlate(context.Background(), "s1", plate)
		if err != nil || *got.LicensePlate != want {
			t.Fatalf("%q: expected %s, got %v, %v", plate, want, got, err)
		}
	}
}
