package service_test

import (
	"bytes"
	"context"
	"errors"
	"io"
	"math/rand"
	"sort"
	"sync"
	"testing"
	"time"

	"api/internal/config"
	"api/internal/model"
	"api/internal/repository"
	"api/internal/service"
)

// FakeSessionRepo implements repository.SessionRepository for testing
// It guards its map with a mutex and hands out copies, so concurrent exits
// race on the conditional update like they would on PostgreSQL.
type FakeSessionRepo struct {
	mu        sync.Mutex
	sessions  map[string]*model.Session
	shouldErr bool
	createErr bool
}

func clone(s *model.Session) *model.Session {
	c := *s
	return &c
}

func (f *FakeSessionRepo) Create(ctx context.Context, s *model.Session) error {
	if f.shouldErr || f.createErr {
		return errors.New("db error")
	}
	f.sessions[s.ID] = s
	return nil
}

func (f *FakeSessionRepo) GetByID(ctx context.Context, id string) (*model.Session, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.shouldErr {
		return nil, errors.New("db error")
	}
	s, ok := f.sessions[id]
	if !ok {
		return nil, nil
	}
	return clone(s), nil
}

func (f *FakeSessionRepo) MarkAsPaid(ctx context.Context, id string, paidAt time.Time, amount float64) (*model.Session, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.shouldErr {
		return nil, errors.New("db error")
	}
	s, ok := f.sessions[id]
	if !ok || s.Status != model.StatusParked {
		return nil, nil
	}
	s.Status = model.StatusPaid
	s.PaidAt = &paidAt
	s.AmountPaid = &amount
	return clone(s), nil
}

func (f *FakeSessionRepo) MarkAsExited(ctx context.Context, id string, exitedAt time.Time) (*model.Session, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.shouldErr {
		return nil, errors.New("db error")
	}
	s, ok := f.sessions[id]
	if !ok || s.Status != model.StatusPaid {
		return nil, nil
	}
	s.Status = model.StatusExited
	s.ExitedAt = &exitedAt
	return clone(s), nil
}

func (f *FakeSessionRepo) FindOccupyingByPlate(ctx context.Context, plate string) ([]*model.Session, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.shouldErr {
		return nil, errors.New("db error")
	}
	var res []*model.Session
	for _, s := range f.sessions {
		if known, ok := s.KnownPlate(); ok && known == plate && s.Occupying() {
			res = append(res, clone(s))
		}
	}
	// Same order as the SQL: oldest payment first, unpaid last, then entry.
	sort.Slice(res, func(i, j int) bool {
		a, b := res[i], res[j]
		if (a.PaidAt == nil) != (b.PaidAt == nil) {
			return a.PaidAt != nil
		}
		if a.PaidAt != nil && !a.PaidAt.Equal(*b.PaidAt) {
			return a.PaidAt.Before(*b.PaidAt)
		}
		return a.EnteredAt.Before(b.EnteredAt)
	})
	return res, nil
}

func (f *FakeSessionRepo) MarkPlateUnreadable(ctx context.Context, id string) error {
	if f.shouldErr {
		return errors.New("db error")
	}
	if s, ok := f.sessions[id]; ok && s.PlateStatus == model.PlatePending {
		s.PlateStatus = model.PlateUnreadable
	}
	return nil
}

func (f *FakeSessionRepo) FindAll(ctx context.Context, filter repository.SessionFilter) ([]*model.Session, error) {
	if f.shouldErr {
		return nil, errors.New("db error")
	}
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

func (f *FakeSessionRepo) UpdatePlate(ctx context.Context, id, plate string) (*model.Session, error) {
	if f.shouldErr {
		return nil, errors.New("db error")
	}
	s, ok := f.sessions[id]
	if !ok || !s.Occupying() {
		return nil, nil
	}
	s.LicensePlate = &plate
	s.PlateStatus = model.PlateManual
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
		if s.Occupying() {
			count++
		}
	}
	return count, nil
}

// FakeSpotsRepo implements repository.SpotsRepository. Like the Redis one, it
// never creates the counter by itself: with missing=true only SetAvailable
// brings it back.
type FakeSpotsRepo struct {
	mu        sync.Mutex
	spots     int64
	missing   bool
	shouldErr bool
}

func (f *FakeSpotsRepo) unavailable() error {
	if f.shouldErr {
		return errors.New("redis error")
	}
	if f.missing {
		return errors.New("key missing in redis")
	}
	return nil
}

func (f *FakeSpotsRepo) GetAvailable(ctx context.Context) (int64, error) {
	if err := f.unavailable(); err != nil {
		return 0, err
	}
	return f.spots, nil
}

func (f *FakeSpotsRepo) SetAvailable(ctx context.Context, count int64) error {
	if f.shouldErr {
		return errors.New("redis error")
	}
	f.spots = count
	f.missing = false
	return nil
}

func (f *FakeSpotsRepo) Increment(ctx context.Context) (int64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if err := f.unavailable(); err != nil {
		return 0, err
	}
	f.spots++
	return f.spots, nil
}

func (f *FakeSpotsRepo) Decrement(ctx context.Context) (int64, error) {
	if err := f.unavailable(); err != nil {
		return 0, err
	}
	if f.spots > 0 {
		f.spots--
	}
	return f.spots, nil
}

func (f *FakeSpotsRepo) Reserve(ctx context.Context) (bool, error) {
	if err := f.unavailable(); err != nil {
		return false, err
	}
	if f.spots <= 0 {
		return false, nil
	}
	f.spots--
	return true, nil
}

// FakeStorage implements repository.BlobStorage
type FakeStorage struct {
	uploaded     map[string][]byte
	contentTypes map[string]string
	deleted      []string
	shouldErr    bool
}

func (f *FakeStorage) Upload(ctx context.Context, key string, body io.Reader, contentType string) error {
	if f.shouldErr {
		return errors.New("s3 upload error")
	}
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
	if f.shouldErr {
		return nil, "", errors.New("s3 download error")
	}
	data, ok := f.uploaded[key]
	if !ok {
		return nil, "", repository.ErrBlobNotFound
	}
	return io.NopCloser(bytes.NewReader(data)), f.contentTypes[key], nil
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
		return errors.New("queue publish error")
	}
	f.published = append(f.published, payload)
	return nil
}

// FakeAuditLogger implements repository.AuditLogger
type FakeAuditLogger struct {
	events  []string
	details []map[string]any
}

func (f *FakeAuditLogger) LogEvent(ctx context.Context, action, entityID string, details map[string]any) error {
	f.events = append(f.events, action)
	f.details = append(f.details, details)
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

func (f *FakeAuditLogger) detailsOf(action string) map[string]any {
	for i, a := range f.events {
		if a == action {
			return f.details[i]
		}
	}
	return nil
}

func hasEvent(audit *FakeAuditLogger, action string) bool {
	return audit.detailsOf(action) != nil
}

func setupService(shouldErrDB, shouldErrStorage bool) (*service.ParkingService, *FakeSessionRepo, *FakeSpotsRepo, *FakeStorage, *FakePublisher, *FakeAuditLogger) {
	repo := &FakeSessionRepo{sessions: make(map[string]*model.Session), shouldErr: shouldErrDB}
	spots := &FakeSpotsRepo{spots: 30}
	storage := &FakeStorage{shouldErr: shouldErrStorage}
	publisher := &FakePublisher{}
	audit := &FakeAuditLogger{}
	cfg := config.Config{TotalParkingSpots: 50, FixedParkingRate: 15.0, ParkingExtraHourRate: 5.0, ParkingToleranceMinutes: 15}

	svc := service.NewParkingService(repo, spots, storage, publisher, audit, cfg)
	return svc, repo, spots, storage, publisher, audit
}

func photo() io.Reader {
	return bytes.NewReader([]byte("test-photo-data"))
}

// ---------------------------------------------------------------------------
// Available spots
// ---------------------------------------------------------------------------

func TestGetAvailableSpots_Success(t *testing.T) {
	svc, _, _, _, _, _ := setupService(false, false)

	available, err := svc.GetAvailableSpots(context.Background())
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
	if available != 30 {
		t.Fatalf("expected 30 spots, got %d", available)
	}
}

func TestGetAvailableSpots_FullLotIsZeroNotAnError(t *testing.T) {
	svc, _, spots, _, _, _ := setupService(false, false)
	spots.spots = 0

	available, err := svc.GetAvailableSpots(context.Background())
	if err != nil || available != 0 {
		t.Fatalf("expected 0 spots and no error, got %d, %v", available, err)
	}
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
	// 12 cars inside: every PARKED session holds a spot, whatever happened
	// to its plate.
	plateStatuses := []string{model.PlateRead, model.PlatePending, model.PlateUnreadable, model.PlateManual}
	for i := 0; i < 12; i++ {
		id := string(rune('a' + i))
		repo.sessions[id] = &model.Session{ID: id, Status: model.StatusParked, PlateStatus: plateStatuses[i%len(plateStatuses)]}
	}
	repo.sessions["paid1"] = &model.Session{ID: "paid1", Status: model.StatusPaid}   // paid, still inside
	repo.sessions["gone1"] = &model.Session{ID: "gone1", Status: model.StatusExited} // already left

	// Redis rebooted: the counter key is gone.
	spots := &FakeSpotsRepo{missing: true}
	cfg := config.Config{TotalParkingSpots: 50}
	svc := service.NewParkingService(repo, spots, nil, nil, nil, cfg)

	available, err := svc.GetAvailableSpots(context.Background())
	if err != nil {
		t.Fatalf("expected successful recovery from DB, got %v", err)
	}
	// 50 - 12 PARKED - 1 PAID (the EXITED car freed its spot).
	if available != 37 {
		t.Fatalf("expected 37 recovered spots, got %d", available)
	}
	if spots.missing || spots.spots != 37 {
		t.Fatalf("expected redis to be rebuilt with 37, got %d (missing=%v)", spots.spots, spots.missing)
	}
}

// ---------------------------------------------------------------------------
// Entry: the ticket is valid and the spot is taken at once
// ---------------------------------------------------------------------------

func TestCreateEntry_Success(t *testing.T) {
	svc, repo, spots, storage, publisher, audit := setupService(false, false)

	session, err := svc.CreateEntry(context.Background(), "car.jpg", photo(), "image/jpeg")
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
	if session.ID == "" {
		t.Fatal("expected session ID to be populated")
	}
	if session.Status != model.StatusParked {
		t.Fatalf("expected status PARKED right at the entry, got %s", session.Status)
	}
	if session.PlateStatus != model.PlatePending {
		t.Fatalf("expected plate_status PENDING, got %s", session.PlateStatus)
	}
	if session.LicensePlate != nil {
		t.Fatalf("expected no plate yet, got %s", *session.LicensePlate)
	}
	if spots.spots != 29 {
		t.Fatalf("expected the spot reserved at the entry (29), got %d", spots.spots)
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
	if queued := audit.details[0]["ocr_queued"]; queued != true {
		t.Fatalf("expected ocr_queued=true in the audit, got %v", queued)
	}
}

func TestCreateEntry_NilPhoto(t *testing.T) {
	svc, _, spots, _, _, _ := setupService(false, false)

	_, err := svc.CreateEntry(context.Background(), "car.jpg", nil, "image/jpeg")
	if !errors.Is(err, service.ErrInvalidPhoto) {
		t.Fatalf("expected ErrInvalidPhoto, got %v", err)
	}
	if spots.spots != 30 {
		t.Fatalf("expected no spot taken without a photo, got %d", spots.spots)
	}
}

func TestCreateEntry_TakesTheLastSpot(t *testing.T) {
	svc, _, spots, _, _, _ := setupService(false, false)
	spots.spots = 1

	if _, err := svc.CreateEntry(context.Background(), "car.jpg", photo(), "image/jpeg"); err != nil {
		t.Fatalf("expected the last spot to be granted, got %v", err)
	}
	if spots.spots != 0 {
		t.Fatalf("expected 0 spots left, got %d", spots.spots)
	}
}

func TestCreateEntry_FullLotIsRefused(t *testing.T) {
	svc, repo, spots, storage, publisher, audit := setupService(false, false)
	spots.spots = 0

	session, err := svc.CreateEntry(context.Background(), "car.jpg", photo(), "image/jpeg")
	if !errors.Is(err, service.ErrParkingFull) {
		t.Fatalf("expected ErrParkingFull, got %v", err)
	}
	if session != nil {
		t.Fatalf("expected no session, got %+v", session)
	}
	if spots.spots != 0 {
		t.Fatalf("expected the counter to stay at 0, got %d", spots.spots)
	}
	if len(repo.sessions) != 0 || len(storage.uploaded) != 0 || len(publisher.published) != 0 || len(audit.events) != 0 {
		t.Fatal("expected nothing stored, published or audited for a refused entry")
	}
}

func TestCreateEntry_MissingCounterFallsBackToDatabase(t *testing.T) {
	svc, repo, spots, _, _, _ := setupService(false, false)
	spots.missing = true
	for _, id := range []string{"a", "b", "c"} {
		repo.sessions[id] = &model.Session{ID: id, Status: model.StatusParked}
	}

	session, err := svc.CreateEntry(context.Background(), "car.jpg", photo(), "image/jpeg")
	if err != nil {
		t.Fatalf("expected the entry to go through with the database count, got %v", err)
	}
	if session.Status != model.StatusParked {
		t.Fatalf("expected PARKED, got %s", session.Status)
	}
	// 50 total - 3 already inside - this car.
	if spots.missing || spots.spots != 46 {
		t.Fatalf("expected the counter rebuilt with 46, got %d (missing=%v)", spots.spots, spots.missing)
	}
}

func TestCreateEntry_MissingCounterAndFullDatabaseIsRefused(t *testing.T) {
	repo := &FakeSessionRepo{sessions: make(map[string]*model.Session)}
	repo.sessions["a"] = &model.Session{ID: "a", Status: model.StatusParked}
	repo.sessions["b"] = &model.Session{ID: "b", Status: model.StatusParked}
	spots := &FakeSpotsRepo{missing: true}
	svc := service.NewParkingService(repo, spots, &FakeStorage{}, &FakePublisher{}, &FakeAuditLogger{}, config.Config{TotalParkingSpots: 2})

	_, err := svc.CreateEntry(context.Background(), "car.jpg", photo(), "image/jpeg")
	if !errors.Is(err, service.ErrParkingFull) {
		t.Fatalf("expected ErrParkingFull, got %v", err)
	}
	if len(repo.sessions) != 2 {
		t.Fatalf("expected no new session, got %d", len(repo.sessions))
	}
}

func TestCreateEntry_RedisDownStillAdmitsByDatabase(t *testing.T) {
	svc, repo, spots, _, _, _ := setupService(false, false)
	spots.shouldErr = true

	session, err := svc.CreateEntry(context.Background(), "car.jpg", photo(), "image/jpeg")
	if err != nil {
		t.Fatalf("expected the entry to survive a Redis outage, got %v", err)
	}
	if _, ok := repo.sessions[session.ID]; !ok {
		t.Fatal("expected the session saved")
	}
}

func TestCreateEntry_StorageFailureGivesTheSpotBack(t *testing.T) {
	svc, repo, spots, _, _, _ := setupService(false, true)

	_, err := svc.CreateEntry(context.Background(), "car.jpg", photo(), "image/jpeg")
	if err == nil {
		t.Fatal("expected storage failure error, got nil")
	}
	if spots.spots != 30 {
		t.Fatalf("expected the reserved spot released (30), got %d", spots.spots)
	}
	if len(repo.sessions) != 0 {
		t.Fatalf("expected no session, got %d", len(repo.sessions))
	}
}

func TestCreateEntry_DatabaseFailureGivesTheSpotBack(t *testing.T) {
	svc, repo, spots, _, publisher, _ := setupService(false, false)
	repo.createErr = true

	_, err := svc.CreateEntry(context.Background(), "car.jpg", photo(), "image/jpeg")
	if err == nil {
		t.Fatal("expected database failure error, got nil")
	}
	if spots.spots != 30 {
		t.Fatalf("expected the reserved spot released (30), got %d", spots.spots)
	}
	if len(publisher.published) != 0 {
		t.Fatal("expected nothing published for a session that was not saved")
	}
}

// The queue being down costs only the plate reading: the car is in, the ticket
// works and nothing is left waiting for a worker that will never get the photo.
func TestCreateEntry_PublishFailureKeepsTheEntry(t *testing.T) {
	svc, repo, spots, _, pub, audit := setupService(false, false)
	pub.shouldErr = true

	session, err := svc.CreateEntry(context.Background(), "car.jpg", photo(), "image/jpeg")
	if err != nil {
		t.Fatalf("expected the entry to succeed without the queue, got %v", err)
	}
	if session.Status != model.StatusParked || session.PlateStatus != model.PlateUnreadable {
		t.Fatalf("expected PARKED/UNREADABLE, got %s/%s", session.Status, session.PlateStatus)
	}
	stored := repo.sessions[session.ID]
	if stored == nil || stored.Status != model.StatusParked || stored.PlateStatus != model.PlateUnreadable {
		t.Fatalf("expected stored session PARKED/UNREADABLE, got %+v", stored)
	}
	if spots.spots != 29 {
		t.Fatalf("expected the spot to stay taken (29), got %d", spots.spots)
	}
	if hasEvent(audit, "ENTRY_FAILED") {
		t.Fatal("ENTRY_FAILED must not be emitted anymore")
	}
	details := audit.detailsOf("ENTRY")
	if details == nil {
		t.Fatalf("expected ENTRY audit event, got %v", audit.events)
	}
	if details["ocr_queued"] != false || details["queue_error"] == nil {
		t.Fatalf("expected ocr_queued=false with queue_error, got %v", details)
	}

	// And the ticket pays normally.
	paid, err := svc.PayExit(context.Background(), session.ID)
	if err != nil || paid.Status != model.StatusPaid {
		t.Fatalf("expected the ticket to pay, got %+v, %v", paid, err)
	}
}

// ---------------------------------------------------------------------------
// Payment: by ticket, with or without a plate
// ---------------------------------------------------------------------------

func TestPayExit_Success(t *testing.T) {
	svc, repo, spots, _, _, audit := setupService(false, false)

	plate := "ABC1D23"
	repo.sessions["s1"] = &model.Session{
		ID:           "s1",
		LicensePlate: &plate,
		Status:       model.StatusParked,
		PlateStatus:  model.PlateRead,
		S3PhotoKey:   "photos/s1.jpg",
		EnteredAt:    time.Now().Add(-100 * time.Minute),
	}

	paid, err := svc.PayExit(context.Background(), "s1")
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
	if paid.Status != model.StatusPaid {
		t.Fatalf("expected status PAID, got %s", paid.Status)
	}
	if paid.AmountPaid == nil || *paid.AmountPaid != 20.0 {
		// 1 h 40 min: 1ª hora (15) + 1 hora extra, por fração (5).
		t.Fatalf("expected amount 20.0, got %v", paid.AmountPaid)
	}
	// The car is still inside: paying keeps its spot.
	if spots.spots != 30 {
		t.Fatalf("expected spots unchanged (30), got %d", spots.spots)
	}
	if paid.PaidAt == nil || paid.ExitedAt != nil {
		t.Fatalf("expected paid_at set and no exit yet, got %v / %v", paid.PaidAt, paid.ExitedAt)
	}
	if len(audit.events) != 1 || audit.events[0] != "EXIT_PAYMENT" {
		t.Fatalf("expected EXIT_PAYMENT audit event, got %v", audit.events)
	}
}

// The heart of D20: a car whose photo was never read (or is still in the
// queue) pays with its ticket, no manual validation.
func TestPayExit_WithoutPlate(t *testing.T) {
	for _, plateStatus := range []string{model.PlatePending, model.PlateUnreadable} {
		svc, repo, spots, _, _, audit := setupService(false, false)
		repo.sessions["s1"] = &model.Session{
			ID:          "s1",
			Status:      model.StatusParked,
			PlateStatus: plateStatus,
			S3PhotoKey:  "photos/blurry.jpg",
			EnteredAt:   time.Now().Add(-1 * time.Hour),
		}

		paid, err := svc.PayExit(context.Background(), "s1")
		if err != nil {
			t.Fatalf("%s: expected the ticket to pay without a plate, got %v", plateStatus, err)
		}
		if paid.Status != model.StatusPaid || paid.LicensePlate != nil {
			t.Fatalf("%s: expected PAID with no plate, got %s %v", plateStatus, paid.Status, paid.LicensePlate)
		}
		if paid.PlateStatus != plateStatus {
			t.Fatalf("%s: expected plate_status kept, got %s", plateStatus, paid.PlateStatus)
		}
		if spots.spots != 30 {
			t.Fatalf("%s: expected the spot kept until the exit barrier (30), got %d", plateStatus, spots.spots)
		}
		if !hasEvent(audit, "EXIT_PAYMENT") {
			t.Fatalf("%s: expected EXIT_PAYMENT audit event", plateStatus)
		}
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

func TestPayExit_AlreadyPaid(t *testing.T) {
	svc, repo, spots, _, _, _ := setupService(false, false)
	repo.sessions["paid"] = &model.Session{ID: "paid", Status: model.StatusPaid, PlateStatus: model.PlateRead}

	_, err := svc.PayExit(context.Background(), "paid")
	if !errors.Is(err, service.ErrInvalidSessionStatus) {
		t.Fatalf("expected ErrInvalidSessionStatus, got %v", err)
	}
	if spots.spots != 30 {
		t.Fatalf("expected the counter untouched by a double payment, got %d", spots.spots)
	}
}

// ---------------------------------------------------------------------------
// Plate typed by the cashier: a convenience, never a requirement
// ---------------------------------------------------------------------------

func TestUpdatePlate_FillsAnUnreadPlateWithoutTouchingTheCounter(t *testing.T) {
	for _, plateStatus := range []string{model.PlatePending, model.PlateUnreadable} {
		svc, repo, spots, _, _, audit := setupService(false, false)
		repo.sessions["s1"] = &model.Session{ID: "s1", Status: model.StatusParked, PlateStatus: plateStatus, EnteredAt: time.Now()}

		got, err := svc.UpdatePlate(context.Background(), "s1", " abc-1d23 ")
		if err != nil {
			t.Fatalf("%s: expected success, got %v", plateStatus, err)
		}
		if got.Status != model.StatusParked || got.LicensePlate == nil || *got.LicensePlate != "ABC1D23" {
			t.Fatalf("%s: expected PARKED with ABC1D23, got %s %v", plateStatus, got.Status, got.LicensePlate)
		}
		if got.PlateStatus != model.PlateManual {
			t.Fatalf("%s: expected plate_status MANUAL, got %s", plateStatus, got.PlateStatus)
		}
		// The spot was taken at the entry; typing the plate must not take another.
		if spots.spots != 30 {
			t.Fatalf("%s: expected spots unchanged (30), got %d", plateStatus, spots.spots)
		}
		details := audit.detailsOf("PLATE_CORRECTION")
		if details == nil {
			t.Fatalf("%s: expected PLATE_CORRECTION audit event", plateStatus)
		}
		if details["previous_plate_status"] != plateStatus || details["previous_plate"] != nil {
			t.Fatalf("%s: unexpected audit details %v", plateStatus, details)
		}
	}
}

func TestUpdatePlate_CorrectsAMisreadPlate(t *testing.T) {
	svc, repo, spots, _, _, audit := setupService(false, false)
	old := "ABC1234"
	repo.sessions["s1"] = &model.Session{ID: "s1", Status: model.StatusParked, PlateStatus: model.PlateRead, LicensePlate: &old}

	got, err := svc.UpdatePlate(context.Background(), "s1", "LSN4I49")
	if err != nil {
		t.Fatalf("expected success, got %v", err)
	}
	if *got.LicensePlate != "LSN4I49" || got.PlateStatus != model.PlateManual || spots.spots != 30 {
		t.Fatalf("expected LSN4I49/MANUAL and 30 spots, got %s/%s and %d", *got.LicensePlate, got.PlateStatus, spots.spots)
	}
	details := audit.detailsOf("PLATE_CORRECTION")
	if details["previous_plate"] != "ABC1234" || details["previous_plate_status"] != model.PlateRead {
		t.Fatalf("unexpected audit details %v", details)
	}
}

func TestUpdatePlate_RejectsInvalidPlate(t *testing.T) {
	svc, repo, _, _, _, _ := setupService(false, false)
	repo.sessions["s1"] = &model.Session{ID: "s1", Status: model.StatusParked, PlateStatus: model.PlateUnreadable}

	for _, plate := range []string{"", "AB12345", "1234567", "ABC12345", "AB1234", "ABCDE12"} {
		if _, err := svc.UpdatePlate(context.Background(), "s1", plate); !errors.Is(err, service.ErrInvalidPlate) {
			t.Fatalf("plate %q: expected ErrInvalidPlate, got %v", plate, err)
		}
	}
}

func TestUpdatePlate_OnlyWhileInside(t *testing.T) {
	svc, repo, _, _, _, _ := setupService(false, false)
	repo.sessions["paid"] = &model.Session{ID: "paid", Status: model.StatusPaid, PlateStatus: model.PlateUnreadable}
	repo.sessions["gone"] = &model.Session{ID: "gone", Status: model.StatusExited, PlateStatus: model.PlateUnreadable}

	// A paid car can still gain a plate before it reaches the barrier.
	got, err := svc.UpdatePlate(context.Background(), "paid", "ABC1D23")
	if err != nil || got.Status != model.StatusPaid || got.PlateStatus != model.PlateManual {
		t.Fatalf("expected PAID/MANUAL, got %+v, %v", got, err)
	}
	if _, err := svc.UpdatePlate(context.Background(), "gone", "ABC1D23"); !errors.Is(err, service.ErrSessionNotEditable) {
		t.Fatalf("expected ErrSessionNotEditable, got %v", err)
	}
	if _, err := svc.UpdatePlate(context.Background(), "missing", "ABC1D23"); !errors.Is(err, service.ErrSessionNotFound) {
		t.Fatalf("expected ErrSessionNotFound, got %v", err)
	}
}

func TestUpdatePlate_AcceptsMercosulCountries(t *testing.T) {
	for plate, want := range map[string]string{
		"abc-1d23":  "ABC1D23", // Brasil (Mercosul)
		"ABC 1234":  "ABC1234", // Brasil (antiga) / Uruguai
		"AA 562 AN": "AA562AN", // Argentina (Mercosul)
		"MWV 724":   "MWV724",  // Argentina (antiga)
		"ABCD 123":  "ABCD123", // Paraguai
	} {
		svc, repo, _, _, _, _ := setupService(false, false)
		repo.sessions["s1"] = &model.Session{ID: "s1", Status: model.StatusParked, PlateStatus: model.PlateUnreadable}
		got, err := svc.UpdatePlate(context.Background(), "s1", plate)
		if err != nil || *got.LicensePlate != want {
			t.Fatalf("%q: expected %s, got %v, %v", plate, want, got, err)
		}
	}
}

// ---------------------------------------------------------------------------
// Delete
// ---------------------------------------------------------------------------

func TestDeleteSession_ParkedFreesTheSpotAndRemovesThePhoto(t *testing.T) {
	// Any PARKED session holds a spot, plate read or not.
	for _, plateStatus := range []string{model.PlateRead, model.PlatePending, model.PlateUnreadable} {
		svc, repo, spots, storage, _, audit := setupService(false, false)
		repo.sessions["s1"] = &model.Session{ID: "s1", Status: model.StatusParked, PlateStatus: plateStatus, S3PhotoKey: "photos/s1_car.jpg"}

		deleted, err := svc.DeleteSession(context.Background(), "s1")
		if err != nil {
			t.Fatalf("%s: expected success, got %v", plateStatus, err)
		}
		if deleted.ID != "s1" {
			t.Fatalf("%s: expected deleted session s1, got %s", plateStatus, deleted.ID)
		}
		if _, ok := repo.sessions["s1"]; ok {
			t.Fatalf("%s: expected session removed from the repository", plateStatus)
		}
		if spots.spots != 31 {
			t.Fatalf("%s: expected spots 31, got %d", plateStatus, spots.spots)
		}
		if len(storage.deleted) != 1 || storage.deleted[0] != "photos/s1_car.jpg" {
			t.Fatalf("%s: expected photo deleted, got %v", plateStatus, storage.deleted)
		}
		if !hasEvent(audit, "SESSION_DELETE") {
			t.Fatalf("%s: expected SESSION_DELETE audit event", plateStatus)
		}
	}
}

func TestDeleteSession_PaidFreesTheSpot(t *testing.T) {
	svc, repo, spots, _, _, _ := setupService(false, false)
	repo.sessions["s1"] = &model.Session{ID: "s1", Status: model.StatusPaid, PlateStatus: model.PlateRead}

	if _, err := svc.DeleteSession(context.Background(), "s1"); err != nil {
		t.Fatalf("expected success, got %v", err)
	}
	// A paid car is still inside until the barrier: deleting it frees its spot.
	if spots.spots != 31 {
		t.Fatalf("expected spots 31, got %d", spots.spots)
	}
}

func TestDeleteSession_ExitedKeepsTheCounter(t *testing.T) {
	svc, repo, spots, _, _, _ := setupService(false, false)
	repo.sessions["s1"] = &model.Session{ID: "s1", Status: model.StatusExited, PlateStatus: model.PlateRead}

	if _, err := svc.DeleteSession(context.Background(), "s1"); err != nil {
		t.Fatalf("expected success, got %v", err)
	}
	// The spot was already given back at the exit barrier.
	if spots.spots != 30 {
		t.Fatalf("expected spots unchanged (30), got %d", spots.spots)
	}
}

func TestDeleteSession_NotFound(t *testing.T) {
	svc, _, _, _, _, _ := setupService(false, false)

	if _, err := svc.DeleteSession(context.Background(), "missing"); !errors.Is(err, service.ErrSessionNotFound) {
		t.Fatalf("expected ErrSessionNotFound, got %v", err)
	}
}

// ---------------------------------------------------------------------------
// Entry photo as evidence
// ---------------------------------------------------------------------------

func TestGetSessionPhoto_ReturnsTheStoredPhoto(t *testing.T) {
	svc, _, _, _, _, _ := setupService(false, false)
	session, err := svc.CreateEntry(context.Background(), "car.jpg", photo(), "image/jpeg")
	if err != nil {
		t.Fatalf("failed to create entry: %v", err)
	}

	body, contentType, err := svc.GetSessionPhoto(context.Background(), session.ID)
	if err != nil {
		t.Fatalf("expected the photo, got %v", err)
	}
	defer body.Close()
	data, _ := io.ReadAll(body)
	if string(data) != "test-photo-data" || contentType != "image/jpeg" {
		t.Fatalf("unexpected photo: %q (%s)", data, contentType)
	}
}

// The content type is whatever the uploader said; anything a browser could run
// is served as a plain download.
func TestGetSessionPhoto_DowngradesUnsafeContentTypes(t *testing.T) {
	for _, uploaded := range []string{"text/html", "image/svg+xml", ""} {
		svc, _, _, _, _, _ := setupService(false, false)
		session, err := svc.CreateEntry(context.Background(), "car.jpg", photo(), uploaded)
		if err != nil {
			t.Fatalf("failed to create entry: %v", err)
		}
		body, contentType, err := svc.GetSessionPhoto(context.Background(), session.ID)
		if err != nil {
			t.Fatalf("%q: expected the photo, got %v", uploaded, err)
		}
		body.Close()
		if contentType != "application/octet-stream" {
			t.Fatalf("%q: expected application/octet-stream, got %s", uploaded, contentType)
		}
	}
}

func TestGetSessionPhoto_Errors(t *testing.T) {
	svc, repo, _, _, _, _ := setupService(false, false)
	repo.sessions["gone"] = &model.Session{ID: "gone", Status: model.StatusParked, S3PhotoKey: "photos/gone.jpg"}
	repo.sessions["nokey"] = &model.Session{ID: "nokey", Status: model.StatusParked}

	if _, _, err := svc.GetSessionPhoto(context.Background(), "missing"); !errors.Is(err, service.ErrSessionNotFound) {
		t.Fatalf("expected ErrSessionNotFound, got %v", err)
	}
	for _, id := range []string{"gone", "nokey"} {
		if _, _, err := svc.GetSessionPhoto(context.Background(), id); !errors.Is(err, service.ErrPhotoNotFound) {
			t.Fatalf("%s: expected ErrPhotoNotFound, got %v", id, err)
		}
	}
}

func TestGetSessionPhoto_StorageError(t *testing.T) {
	svc, repo, _, storage, _, _ := setupService(false, false)
	repo.sessions["s1"] = &model.Session{ID: "s1", Status: model.StatusParked, S3PhotoKey: "photos/s1.jpg"}
	storage.shouldErr = true

	_, _, err := svc.GetSessionPhoto(context.Background(), "s1")
	if err == nil || errors.Is(err, service.ErrPhotoNotFound) {
		t.Fatalf("expected a storage error, got %v", err)
	}
}

// ---------------------------------------------------------------------------
// Listing
// ---------------------------------------------------------------------------

func TestFindSessions_FiltersByPlateStatus(t *testing.T) {
	svc, repo, _, _, _, _ := setupService(false, false)
	plate := "ABC1D23"
	repo.sessions["read"] = &model.Session{ID: "read", Status: model.StatusParked, PlateStatus: model.PlateRead, LicensePlate: &plate}
	repo.sessions["unread"] = &model.Session{ID: "unread", Status: model.StatusParked, PlateStatus: model.PlateUnreadable}
	repo.sessions["paid"] = &model.Session{ID: "paid", Status: model.StatusPaid, PlateStatus: model.PlateUnreadable}

	got, err := svc.FindSessions(context.Background(), repository.SessionFilter{Status: model.StatusParked, PlateStatus: model.PlateUnreadable})
	if err != nil || len(got) != 1 || got[0].ID != "unread" {
		t.Fatalf("expected only the unread PARKED session, got %v, %v", got, err)
	}

	got, err = svc.FindSessions(context.Background(), repository.SessionFilter{Plate: "ABC1D23"})
	if err != nil || len(got) != 1 || got[0].ID != "read" {
		t.Fatalf("expected the session found by plate, got %v, %v", got, err)
	}
}

// ---------------------------------------------------------------------------
// Exit barrier
// ---------------------------------------------------------------------------

func strptr(s string) *string { return &s }

func timeAgo(d time.Duration) *time.Time {
	t := time.Now().Add(-d)
	return &t
}

func TestExitByTicket_PaidCarLeavesAndFreesTheSpot(t *testing.T) {
	for _, plateStatus := range []string{model.PlatePending, model.PlateRead, model.PlateUnreadable, model.PlateManual} {
		svc, repo, spots, _, _, audit := setupService(false, false)
		repo.sessions["s1"] = &model.Session{ID: "s1", Status: model.StatusPaid, PlateStatus: plateStatus, PaidAt: timeAgo(time.Minute)}

		exited, err := svc.ExitBarrierByTicket(context.Background(), " s1 ")
		if err != nil {
			t.Fatalf("%s: expected the barrier to open, got %v", plateStatus, err)
		}
		if exited.Status != model.StatusExited || exited.ExitedAt == nil {
			t.Fatalf("%s: expected EXITED with exited_at, got %+v", plateStatus, exited)
		}
		if spots.spots != 31 {
			t.Fatalf("%s: expected the spot given back (31), got %d", plateStatus, spots.spots)
		}
		details := audit.detailsOf("EXIT_BARRIER_OPENED")
		if details == nil || details["method"] != "ticket" || details["status"] != model.StatusExited {
			t.Fatalf("%s: unexpected audit %v / %v", plateStatus, audit.events, details)
		}
	}
}

func TestExitByTicket_Refusals(t *testing.T) {
	cases := []struct {
		name   string
		status string
		ticket string
		want   error
	}{
		{"unpaid", model.StatusParked, "s1", service.ErrPaymentPending},
		{"already left", model.StatusExited, "s1", service.ErrAlreadyExited},
		{"unknown ticket", model.StatusPaid, "nope", service.ErrSessionNotFound},
		{"empty ticket", model.StatusPaid, "  ", service.ErrInvalidTicket},
	}
	for _, tc := range cases {
		svc, repo, spots, _, _, audit := setupService(false, false)
		repo.sessions["s1"] = &model.Session{ID: "s1", Status: tc.status, PlateStatus: model.PlateRead}

		_, err := svc.ExitBarrierByTicket(context.Background(), tc.ticket)
		if !errors.Is(err, tc.want) {
			t.Fatalf("%s: expected %v, got %v", tc.name, tc.want, err)
		}
		if repo.sessions["s1"].Status != tc.status || spots.spots != 30 || len(audit.events) != 0 {
			t.Fatalf("%s: expected nothing changed, got %s, %d spots, %v", tc.name, repo.sessions["s1"].Status, spots.spots, audit.events)
		}
	}
}

func TestExitByTicket_DatabaseError(t *testing.T) {
	svc, _, _, _, _, _ := setupService(true, false)
	if _, err := svc.ExitBarrierByTicket(context.Background(), "s1"); err == nil {
		t.Fatal("expected a database error")
	}
}

func TestExitByPlate_PaidCarLeaves(t *testing.T) {
	for _, plateStatus := range []string{model.PlateRead, model.PlateManual} {
		svc, repo, spots, _, _, audit := setupService(false, false)
		repo.sessions["s1"] = &model.Session{
			ID: "s1", Status: model.StatusPaid, PlateStatus: plateStatus,
			LicensePlate: strptr("ABC1D23"), PaidAt: timeAgo(time.Minute),
		}

		exited, err := svc.ExitBarrierByPlate(context.Background(), "abc-1d23")
		if err != nil || exited.ID != "s1" || exited.Status != model.StatusExited {
			t.Fatalf("%s: expected s1 EXITED, got %+v, %v", plateStatus, exited, err)
		}
		if spots.spots != 31 {
			t.Fatalf("%s: expected spots 31, got %d", plateStatus, spots.spots)
		}
		details := audit.detailsOf("EXIT_BARRIER_OPENED")
		if details["method"] != "plate" || details["license_plate"] != "ABC1D23" {
			t.Fatalf("%s: unexpected audit details %v", plateStatus, details)
		}
	}
}

func TestExitByPlate_UnpaidCarIsRefused(t *testing.T) {
	svc, repo, spots, _, _, audit := setupService(false, false)
	repo.sessions["s1"] = &model.Session{ID: "s1", Status: model.StatusParked, PlateStatus: model.PlateRead, LicensePlate: strptr("ABC1D23")}

	if _, err := svc.ExitBarrierByPlate(context.Background(), "ABC1D23"); !errors.Is(err, service.ErrPaymentPending) {
		t.Fatalf("expected ErrPaymentPending, got %v", err)
	}
	if repo.sessions["s1"].Status != model.StatusParked || spots.spots != 30 || len(audit.events) != 0 {
		t.Fatal("expected nothing changed")
	}
}

// A plate the OCR has not confirmed, a car that already left or an unknown
// plate never opens the barrier: the driver uses the ticket.
func TestExitByPlate_NotFound(t *testing.T) {
	svc, repo, spots, _, _, _ := setupService(false, false)
	repo.sessions["pending"] = &model.Session{ID: "pending", Status: model.StatusPaid, PlateStatus: model.PlatePending, LicensePlate: strptr("ABC1D23")}
	repo.sessions["unreadable"] = &model.Session{ID: "unreadable", Status: model.StatusPaid, PlateStatus: model.PlateUnreadable, LicensePlate: strptr("ABC1D23")}
	repo.sessions["gone"] = &model.Session{ID: "gone", Status: model.StatusExited, PlateStatus: model.PlateRead, LicensePlate: strptr("ABC1D23")}

	for _, plate := range []string{"ABC1D23", "LSN4I49"} {
		if _, err := svc.ExitBarrierByPlate(context.Background(), plate); !errors.Is(err, service.ErrPlateNotFound) {
			t.Fatalf("%s: expected ErrPlateNotFound, got %v", plate, err)
		}
	}
	if spots.spots != 30 {
		t.Fatalf("expected spots unchanged, got %d", spots.spots)
	}

	// The paper ticket still works for the car whose plate was never read.
	if _, err := svc.ExitBarrierByTicket(context.Background(), "unreadable"); err != nil {
		t.Fatalf("expected the ticket to open the barrier, got %v", err)
	}
}

func TestExitByPlate_InvalidPlate(t *testing.T) {
	svc, _, _, _, _, _ := setupService(false, false)
	for _, plate := range []string{"", "AB12345", "123"} {
		if _, err := svc.ExitBarrierByPlate(context.Background(), plate); !errors.Is(err, service.ErrInvalidPlate) {
			t.Fatalf("%q: expected ErrInvalidPlate, got %v", plate, err)
		}
	}
}

// Two cars with the same plate: the paid one leaves even if the unpaid one
// entered first, and among paid ones the first to pay leaves first.
func TestExitByPlate_RepeatedPlate(t *testing.T) {
	svc, repo, spots, _, _, _ := setupService(false, false)
	repo.sessions["unpaid"] = &model.Session{ID: "unpaid", Status: model.StatusParked, PlateStatus: model.PlateRead,
		LicensePlate: strptr("ABC1D23"), EnteredAt: time.Now().Add(-3 * time.Hour)}
	repo.sessions["paid-late"] = &model.Session{ID: "paid-late", Status: model.StatusPaid, PlateStatus: model.PlateManual,
		LicensePlate: strptr("ABC1D23"), EnteredAt: time.Now().Add(-2 * time.Hour), PaidAt: timeAgo(time.Minute)}
	repo.sessions["paid-early"] = &model.Session{ID: "paid-early", Status: model.StatusPaid, PlateStatus: model.PlateRead,
		LicensePlate: strptr("ABC1D23"), EnteredAt: time.Now().Add(-1 * time.Hour), PaidAt: timeAgo(10 * time.Minute)}

	for _, want := range []string{"paid-early", "paid-late"} {
		exited, err := svc.ExitBarrierByPlate(context.Background(), "ABC1D23")
		if err != nil || exited.ID != want {
			t.Fatalf("expected %s to leave, got %+v, %v", want, exited, err)
		}
	}
	if _, err := svc.ExitBarrierByPlate(context.Background(), "ABC1D23"); !errors.Is(err, service.ErrPaymentPending) {
		t.Fatalf("expected the unpaid car to be refused, got %v", err)
	}
	if repo.sessions["unpaid"].Status != model.StatusParked || spots.spots != 32 {
		t.Fatalf("expected unpaid kept and 32 spots, got %s and %d", repo.sessions["unpaid"].Status, spots.spots)
	}
}

// Property 2: whatever the interleaving, a car frees its spot once and only
// one request opens the barrier.
func TestExit_ConcurrentRequestsReleaseTheSpotOnce(t *testing.T) {
	svc, repo, spots, _, _, audit := setupService(false, false)
	repo.sessions["s1"] = &model.Session{ID: "s1", Status: model.StatusPaid, PlateStatus: model.PlateRead,
		LicensePlate: strptr("ABC1D23"), PaidAt: timeAgo(time.Minute)}

	const requests = 20
	var wg sync.WaitGroup
	var mu sync.Mutex
	opened, alreadyExited := 0, 0
	for i := range requests {
		wg.Add(1)
		go func() {
			defer wg.Done()
			var err error
			if i%2 == 0 {
				_, err = svc.ExitBarrierByTicket(context.Background(), "s1")
			} else {
				_, err = svc.ExitBarrierByPlate(context.Background(), "ABC1D23")
			}
			mu.Lock()
			defer mu.Unlock()
			switch {
			case err == nil:
				opened++
			case errors.Is(err, service.ErrAlreadyExited), errors.Is(err, service.ErrPlateNotFound):
				alreadyExited++
			default:
				t.Errorf("unexpected error: %v", err)
			}
		}()
	}
	wg.Wait()

	if opened != 1 || alreadyExited != requests-1 {
		t.Fatalf("expected 1 opening and %d refusals, got %d and %d", requests-1, opened, alreadyExited)
	}
	if spots.spots != 31 {
		t.Fatalf("expected the spot released once (31), got %d", spots.spots)
	}
	count := 0
	for _, e := range audit.events {
		if e == "EXIT_BARRIER_OPENED" {
			count++
		}
	}
	if count != 1 {
		t.Fatalf("expected one EXIT_BARRIER_OPENED, got %d", count)
	}
}

func TestExit_RedisDownStillOpensAndTheCounterIsRebuilt(t *testing.T) {
	svc, repo, spots, _, _, _ := setupService(false, false)
	repo.sessions["s1"] = &model.Session{ID: "s1", Status: model.StatusPaid, PlateStatus: model.PlateRead, PaidAt: timeAgo(time.Minute)}
	repo.sessions["s2"] = &model.Session{ID: "s2", Status: model.StatusParked, PlateStatus: model.PlateRead}
	spots.missing = true

	if _, err := svc.ExitBarrierByTicket(context.Background(), "s1"); err != nil {
		t.Fatalf("expected the barrier to open without the counter, got %v", err)
	}
	// 50 spots, one car (s2) still inside.
	if got, err := svc.GetAvailableSpots(context.Background()); err != nil || got != 49 {
		t.Fatalf("expected the counter rebuilt to 49, got %d, %v", got, err)
	}
}

// Property 1: over any sequence of entries, payments, exits and deletions the
// counter equals the free spots: total minus the cars inside (PARKED + PAID).
// Property 3: no session reaches EXITED without having been PAID.
func TestSpotConservation_RandomSequences(t *testing.T) {
	const total = 5
	for seed := int64(1); seed <= 200; seed++ {
		rng := rand.New(rand.NewSource(seed))
		repo := &FakeSessionRepo{sessions: make(map[string]*model.Session)}
		spots := &FakeSpotsRepo{spots: total}
		svc := service.NewParkingService(repo, spots, &FakeStorage{}, &FakePublisher{}, &FakeAuditLogger{},
			config.Config{TotalParkingSpots: total, FixedParkingRate: 10})
		everPaid := map[string]bool{}

		pick := func(status string) string {
			var ids []string
			for id, s := range repo.sessions {
				if s.Status == status {
					ids = append(ids, id)
				}
			}
			if len(ids) == 0 {
				return ""
			}
			sort.Strings(ids)
			return ids[rng.Intn(len(ids))]
		}

		for step := 0; step < 60; step++ {
			switch rng.Intn(6) {
			case 0, 1:
				_, err := svc.CreateEntry(context.Background(), "car.jpg", photo(), "image/jpeg")
				if err != nil && !errors.Is(err, service.ErrParkingFull) {
					t.Fatalf("seed %d: entry failed: %v", seed, err)
				}
			case 2:
				if id := pick(model.StatusParked); id != "" {
					if _, err := svc.PayExit(context.Background(), id); err != nil {
						t.Fatalf("seed %d: payment failed: %v", seed, err)
					}
					everPaid[id] = true
				}
			case 3:
				if id := pick(model.StatusPaid); id != "" {
					if _, err := svc.ExitBarrierByTicket(context.Background(), id); err != nil {
						t.Fatalf("seed %d: exit failed: %v", seed, err)
					}
				}
			case 4:
				// Trying to leave without paying never works.
				if id := pick(model.StatusParked); id != "" {
					if _, err := svc.ExitBarrierByTicket(context.Background(), id); !errors.Is(err, service.ErrPaymentPending) {
						t.Fatalf("seed %d: unpaid exit: expected ErrPaymentPending, got %v", seed, err)
					}
				}
			case 5:
				ids := make([]string, 0, len(repo.sessions))
				for id := range repo.sessions {
					ids = append(ids, id)
				}
				if len(ids) > 0 {
					sort.Strings(ids)
					if _, err := svc.DeleteSession(context.Background(), ids[rng.Intn(len(ids))]); err != nil {
						t.Fatalf("seed %d: delete failed: %v", seed, err)
					}
				}
			}

			inside, _ := repo.CountActive(context.Background())
			if spots.spots != total-inside {
				t.Fatalf("seed %d step %d: counter %d, expected %d (total %d, inside %d)", seed, step, spots.spots, total-inside, total, inside)
			}
			for id, s := range repo.sessions {
				if s.Status == model.StatusExited && !everPaid[id] {
					t.Fatalf("seed %d: session %s left without paying", seed, id)
				}
			}
		}
	}
}

func TestPayExit_WithinToleranceIsFree(t *testing.T) {
	svc, repo, _, _, _, audit := setupService(false, false)
	repo.sessions["s1"] = &model.Session{ID: "s1", Status: model.StatusParked, PlateStatus: model.PlateRead, EnteredAt: time.Now().Add(-10 * time.Minute)}

	paid, err := svc.PayExit(context.Background(), "s1")
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
	if paid.Status != model.StatusPaid || paid.AmountPaid == nil || *paid.AmountPaid != 0 {
		t.Fatalf("expected PAID with amount 0, got %s / %v", paid.Status, paid.AmountPaid)
	}
	if len(audit.events) != 1 || audit.events[0] != "EXIT_PAYMENT" {
		t.Fatalf("expected EXIT_PAYMENT audit, got %v", audit.events)
	}
}

func TestFindSessions_ParkedCarryAmountDue(t *testing.T) {
	svc, repo, _, _, _, _ := setupService(false, false)
	repo.sessions["free"] = &model.Session{ID: "free", Status: model.StatusParked, EnteredAt: time.Now().Add(-5 * time.Minute)}
	repo.sessions["first"] = &model.Session{ID: "first", Status: model.StatusParked, EnteredAt: time.Now().Add(-40 * time.Minute)}
	repo.sessions["extra"] = &model.Session{ID: "extra", Status: model.StatusParked, EnteredAt: time.Now().Add(-100 * time.Minute)}
	paidAt := time.Now()
	repo.sessions["paid"] = &model.Session{ID: "paid", Status: model.StatusPaid, EnteredAt: time.Now().Add(-3 * time.Hour), PaidAt: &paidAt}

	got, err := svc.FindSessions(context.Background(), repository.SessionFilter{})
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
	// Tarifa do setupService: 1ª hora 15, hora extra 5, tolerância 15 min.
	want := map[string]*float64{"free": f64(0), "first": f64(15), "extra": f64(20), "paid": nil}
	for _, session := range got {
		w, ok := want[session.ID]
		if !ok {
			continue
		}
		switch {
		case w == nil && session.AmountDue != nil:
			t.Fatalf("%s: a paid session must not carry amount_due, got %v", session.ID, *session.AmountDue)
		case w != nil && (session.AmountDue == nil || *session.AmountDue != *w):
			t.Fatalf("%s: expected amount_due %.2f, got %v", session.ID, *w, session.AmountDue)
		}
	}
}

func f64(v float64) *float64 { return &v }
