package service

import (
	"api/internal/config"
	"api/internal/model"
	"api/internal/repository"
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"log"
	"path"
	"regexp"
	"strings"
	"time"
)

var (
	ErrSessionNotFound      = errors.New("session not found")
	ErrInvalidPhoto         = errors.New("photo is required")
	ErrInvalidSessionStatus = errors.New("session is not in PARKED status")
	ErrInvalidPlate         = errors.New("invalid license plate")
	ErrSessionNotEditable   = errors.New("session plate can only be set while the car is inside (PARKED or PAID)")
	ErrParkingFull          = errors.New("parking lot is full")
	ErrPhotoNotFound        = errors.New("session photo not found")
	ErrPaymentPending       = errors.New("payment pending: pay at the cashier before leaving")
	ErrAlreadyExited        = errors.New("the car already left through the exit barrier")
	ErrPlateNotFound        = errors.New("no car inside with this plate: use the ticket")
	ErrInvalidTicket        = errors.New("ticket_id is required")
)

// Plates of the Mercosul countries typed by the cashier, after removing
// spaces/hyphens (docs/DECISOES.md, D8): Brazil ABC1D23 / ABC1234 (also
// Uruguay), Argentina AB123CD / ABC123 and Paraguay ABCD123.
var platePattern = regexp.MustCompile(
	`^([A-Z]{3}[0-9][A-Z0-9][0-9]{2}|[A-Z]{2}[0-9]{3}[A-Z]{2}|[A-Z]{4}[0-9]{3}|[A-Z]{3}[0-9]{3})$`,
)
var nonAlnum = regexp.MustCompile(`[^A-Z0-9]`)

func normalizePlate(raw string) (string, error) {
	plate := nonAlnum.ReplaceAllString(strings.ToUpper(raw), "")
	if !platePattern.MatchString(plate) {
		return "", ErrInvalidPlate
	}
	return plate, nil
}

type ParkingService struct {
	sessionRepo repository.SessionRepository
	spotsRepo   repository.SpotsRepository
	storage     repository.BlobStorage
	publisher   repository.EventPublisher
	audit       repository.AuditLogger
	totalSpots  int
	pricing     Pricing
}

func NewParkingService(
	sessionRepo repository.SessionRepository,
	spotsRepo repository.SpotsRepository,
	storage repository.BlobStorage,
	publisher repository.EventPublisher,
	audit repository.AuditLogger,
	cfg config.Config,
) *ParkingService {
	return &ParkingService{
		sessionRepo: sessionRepo,
		spotsRepo:   spotsRepo,
		storage:     storage,
		publisher:   publisher,
		audit:       audit,
		totalSpots:  cfg.TotalParkingSpots,
		pricing: Pricing{
			Tolerance: time.Duration(cfg.ParkingToleranceMinutes) * time.Minute,
			FirstHour: cfg.FixedParkingRate,
			ExtraHour: cfg.ParkingExtraHourRate,
		},
	}
}

func (s *ParkingService) GetAvailableSpots(ctx context.Context) (int64, error) {
	spots, err := s.spotsRepo.GetAvailable(ctx)
	if err == nil {
		return spots, nil
	}

	// Redis falhou ou reiniciou (chave inexistente). Reconcilia com o banco relacional para evitar overbooking.
	activeCount, dbErr := s.sessionRepo.CountActive(ctx)
	if dbErr != nil {
		return 0, fmt.Errorf("failed to recover spot count from database: %w", dbErr)
	}

	calculated := int64(s.totalSpots) - activeCount
	if calculated < 0 {
		calculated = 0
	}

	_ = s.spotsRepo.SetAvailable(ctx, calculated)

	return calculated, nil
}

// reserveSpot takes a spot for a car at the entry gate. Redis is the fast
// path; when the counter is missing or Redis is down, PostgreSQL decides (the
// sessions table is the truth) and the counter is rebuilt already counting
// this car.
func (s *ParkingService) reserveSpot(ctx context.Context) error {
	reserved, err := s.spotsRepo.Reserve(ctx)
	if err == nil {
		if !reserved {
			return ErrParkingFull
		}
		return nil
	}

	activeCount, dbErr := s.sessionRepo.CountActive(ctx)
	if dbErr != nil {
		return fmt.Errorf("failed to check free spots: %w", dbErr)
	}
	free := int64(s.totalSpots) - activeCount
	if free <= 0 {
		return ErrParkingFull
	}
	_ = s.spotsRepo.SetAvailable(ctx, free-1)
	return nil
}

// CreateEntry opens a session for a car at the entry gate. The ticket (session
// ID) is what identifies the car from here on: the session is PARKED and holds
// a spot at once, whatever happens to the photo. Reading the plate is left to
// the worker and only fills license_plate/plate_status later.
func (s *ParkingService) CreateEntry(ctx context.Context, photoFileName string, photoBody io.Reader, contentType string) (*model.Session, error) {
	if photoBody == nil {
		return nil, ErrInvalidPhoto
	}

	if err := s.reserveSpot(ctx); err != nil {
		return nil, err
	}

	sessionID := generateID()
	s3Key := fmt.Sprintf("photos/%s_%s", sessionID, safeFileName(photoFileName))

	if err := s.storage.Upload(ctx, s3Key, photoBody, contentType); err != nil {
		_, _ = s.spotsRepo.Increment(ctx)
		return nil, fmt.Errorf("failed to upload photo: %w", err)
	}

	session := &model.Session{
		ID:          sessionID,
		Status:      model.StatusParked,
		PlateStatus: model.PlatePending,
		S3PhotoKey:  s3Key,
		EnteredAt:   time.Now().UTC(),
	}

	if err := s.sessionRepo.Create(ctx, session); err != nil {
		_, _ = s.spotsRepo.Increment(ctx)
		return nil, fmt.Errorf("failed to create session: %w", err)
	}

	details := map[string]any{
		"s3_photo_key": s3Key,
		"status":       session.Status,
		"ocr_queued":   true,
	}
	if err := s.publisher.Publish(ctx, map[string]string{
		"session_id": sessionID,
		"s3_key":     s3Key,
	}); err != nil {
		// The car is in and the ticket is valid; only the plate reading is
		// lost. Nothing is left waiting for a worker that will never come.
		log.Printf("[WARN] session %s: photo not queued for OCR: %v", sessionID, err)
		_ = s.sessionRepo.MarkPlateUnreadable(ctx, sessionID)
		session.PlateStatus = model.PlateUnreadable
		details["ocr_queued"] = false
		details["queue_error"] = err.Error()
	}

	_ = s.audit.LogEvent(ctx, "ENTRY", sessionID, details)

	return session, nil
}

// PayExit charges a PARKED session by its ticket ID, with or without a plate.
// The amount depends on how long the car stayed (see CalculateFee); a stay
// within the tolerance is paid with R$ 0,00 and still produces the receipt.
// The car is still inside, so it keeps its spot until the exit barrier.
func (s *ParkingService) PayExit(ctx context.Context, sessionID string) (*model.Session, error) {
	session, err := s.sessionRepo.GetByID(ctx, sessionID)
	if err != nil {
		return nil, fmt.Errorf("failed to retrieve session: %w", err)
	}
	if session == nil {
		return nil, ErrSessionNotFound
	}
	if session.Status != model.StatusParked {
		return nil, ErrInvalidSessionStatus
	}

	paidAt := time.Now().UTC()
	amount := CalculateFee(paidAt.Sub(session.EnteredAt), s.pricing)

	paidSession, err := s.sessionRepo.MarkAsPaid(ctx, sessionID, paidAt, amount)
	if err != nil {
		return nil, fmt.Errorf("failed to update session: %w", err)
	}
	if paidSession == nil {
		return nil, ErrInvalidSessionStatus
	}

	_ = s.audit.LogEvent(ctx, "EXIT_PAYMENT", sessionID, map[string]any{
		"amount_paid": amount,
		"status":      paidSession.Status,
	})

	return paidSession, nil
}

// ExitBarrierByTicket opens the exit barrier for the paper ticket. It works
// for any plate status: the ticket is what identifies the car (D20).
func (s *ParkingService) ExitBarrierByTicket(ctx context.Context, ticketID string) (*model.Session, error) {
	ticketID = strings.TrimSpace(ticketID)
	if ticketID == "" {
		return nil, ErrInvalidTicket
	}
	session, err := s.sessionRepo.GetByID(ctx, ticketID)
	if err != nil {
		return nil, fmt.Errorf("failed to retrieve session: %w", err)
	}
	if session == nil {
		return nil, ErrSessionNotFound
	}
	switch session.Status {
	case model.StatusPaid:
		return s.openBarrier(ctx, session, "ticket")
	case model.StatusExited:
		return nil, ErrAlreadyExited
	default:
		return nil, ErrPaymentPending
	}
}

// ExitBarrierByPlate opens the exit barrier for the plate the exit camera
// recognized (or the driver typed). Only a plate read by the OCR or typed by
// the cashier can match, and only a car still inside; among several paid
// cars with the same plate the one that paid first leaves. The plate is a
// shortcut, never a requirement: whatever this route refuses, the ticket
// opens.
func (s *ParkingService) ExitBarrierByPlate(ctx context.Context, rawPlate string) (*model.Session, error) {
	plate, err := normalizePlate(rawPlate)
	if err != nil {
		return nil, err
	}
	sessions, err := s.sessionRepo.FindOccupyingByPlate(ctx, plate)
	if err != nil {
		return nil, fmt.Errorf("failed to find session by plate: %w", err)
	}
	if len(sessions) == 0 {
		return nil, ErrPlateNotFound
	}
	for _, session := range sessions {
		if session.Status == model.StatusPaid {
			return s.openBarrier(ctx, session, "plate")
		}
	}
	return nil, ErrPaymentPending
}

// openBarrier lets a PAID car out and gives its spot back. The conditional
// update decides races: only the request that moved the session to EXITED
// increments the counter, so a spot is released exactly once.
func (s *ParkingService) openBarrier(ctx context.Context, session *model.Session, method string) (*model.Session, error) {
	exited, err := s.sessionRepo.MarkAsExited(ctx, session.ID, time.Now().UTC())
	if err != nil {
		return nil, fmt.Errorf("failed to update session: %w", err)
	}
	if exited == nil {
		return nil, ErrAlreadyExited
	}

	// Best effort: when the counter is missing or Redis is down, the next read
	// rebuilds it from PostgreSQL, where this car is already EXITED.
	_, _ = s.spotsRepo.Increment(ctx)

	var plate any
	if known, ok := exited.KnownPlate(); ok {
		plate = known
	}
	_ = s.audit.LogEvent(ctx, "EXIT_BARRIER_OPENED", exited.ID, map[string]any{
		"status":        exited.Status,
		"method":        method,
		"license_plate": plate,
	})

	return exited, nil
}

// UpdatePlate lets the cashier type a plate the OCR did not read or fix a
// misread one, while the car is inside (PARKED or PAID), so a paid car can
// still leave by plate. It is a convenience, never a requirement: the session
// already holds its spot, so the counter is not touched.
func (s *ParkingService) UpdatePlate(ctx context.Context, sessionID, rawPlate string) (*model.Session, error) {
	plate, err := normalizePlate(rawPlate)
	if err != nil {
		return nil, err
	}
	session, err := s.sessionRepo.GetByID(ctx, sessionID)
	if err != nil {
		return nil, fmt.Errorf("failed to retrieve session: %w", err)
	}
	if session == nil {
		return nil, ErrSessionNotFound
	}
	if !session.Occupying() {
		return nil, ErrSessionNotEditable
	}

	previousPlateStatus := session.PlateStatus
	var previousPlate any
	if session.LicensePlate != nil {
		previousPlate = *session.LicensePlate
	}

	updated, err := s.sessionRepo.UpdatePlate(ctx, sessionID, plate)
	if err != nil {
		return nil, fmt.Errorf("failed to update session: %w", err)
	}
	if updated == nil {
		return nil, ErrSessionNotEditable
	}

	_ = s.audit.LogEvent(ctx, "PLATE_CORRECTION", sessionID, map[string]any{
		"license_plate":         plate,
		"previous_plate":        previousPlate,
		"previous_plate_status": previousPlateStatus,
		"status":                updated.Status,
	})

	return updated, nil
}

// DeleteSession removes a session and its photo (the D of the CRUD). A car
// still inside (PARKED or PAID) gives its spot back; an EXITED one already did
// at the barrier.
func (s *ParkingService) DeleteSession(ctx context.Context, sessionID string) (*model.Session, error) {
	session, err := s.sessionRepo.GetByID(ctx, sessionID)
	if err != nil {
		return nil, fmt.Errorf("failed to retrieve session: %w", err)
	}
	if session == nil {
		return nil, ErrSessionNotFound
	}

	deleted, err := s.sessionRepo.Delete(ctx, sessionID)
	if err != nil {
		return nil, fmt.Errorf("failed to delete session: %w", err)
	}
	if !deleted {
		return nil, ErrSessionNotFound
	}

	if session.Occupying() {
		_, _ = s.spotsRepo.Increment(ctx)
	}
	// Best effort: an orphan photo costs cents, a failed delete must not
	// resurrect the session.
	if session.S3PhotoKey != "" {
		if err := s.storage.Delete(ctx, session.S3PhotoKey); err != nil {
			log.Printf("[WARN] session %s deleted but photo %s was not: %v", sessionID, session.S3PhotoKey, err)
		}
	}

	var plate any
	if session.LicensePlate != nil {
		plate = *session.LicensePlate
	}
	_ = s.audit.LogEvent(ctx, "SESSION_DELETE", sessionID, map[string]any{
		"status":        session.Status,
		"license_plate": plate,
		"s3_photo_key":  session.S3PhotoKey,
	})

	return session, nil
}

// servablePhotoTypes are the content types the API sends back as they were
// stored. The type comes from the client that uploaded the photo, so anything
// else (HTML, SVG) is downgraded to a download instead of being rendered.
var servablePhotoTypes = map[string]bool{
	"image/jpeg": true,
	"image/png":  true,
	"image/webp": true,
	"image/gif":  true,
}

// GetSessionPhoto opens the entry photo of a session: the evidence the cashier
// and the audit trail show next to a ticket. The caller closes the reader.
func (s *ParkingService) GetSessionPhoto(ctx context.Context, sessionID string) (io.ReadCloser, string, error) {
	session, err := s.sessionRepo.GetByID(ctx, sessionID)
	if err != nil {
		return nil, "", fmt.Errorf("failed to retrieve session: %w", err)
	}
	if session == nil {
		return nil, "", ErrSessionNotFound
	}
	if session.S3PhotoKey == "" {
		return nil, "", ErrPhotoNotFound
	}

	body, contentType, err := s.storage.Download(ctx, session.S3PhotoKey)
	if errors.Is(err, repository.ErrBlobNotFound) {
		return nil, "", ErrPhotoNotFound
	}
	if err != nil {
		return nil, "", fmt.Errorf("failed to download photo: %w", err)
	}
	if !servablePhotoTypes[contentType] {
		contentType = "application/octet-stream"
	}
	return body, contentType, nil
}

var unsafeNameChars = regexp.MustCompile(`[^A-Za-z0-9._-]`)

// safeFileName drops any directory part and replaces characters outside
// [A-Za-z0-9._-], so a client filename cannot shape the S3 key.
func safeFileName(name string) string {
	name = unsafeNameChars.ReplaceAllString(path.Base(strings.ReplaceAll(name, `\`, "/")), "_")
	if name == "" || name == "." || name == ".." {
		return "photo"
	}
	return name
}

func generateID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// FindSessions lists sessions. PARKED ones carry AmountDue, the same amount
// PayExit would charge at this moment, so the cashier sees it before confirming.
func (s *ParkingService) FindSessions(ctx context.Context, filter repository.SessionFilter) ([]*model.Session, error) {
	sessions, err := s.sessionRepo.FindAll(ctx, filter)
	if err != nil {
		return nil, err
	}
	now := time.Now().UTC()
	for _, session := range sessions {
		if session.Status == model.StatusParked {
			due := CalculateFee(now.Sub(session.EnteredAt), s.pricing)
			session.AmountDue = &due
		}
	}
	return sessions, nil
}

func (s *ParkingService) GetAuditLogs(ctx context.Context, limit int) ([]*model.AuditLog, error) {
	if limit <= 0 {
		limit = 50
	}
	return s.audit.GetRecentLogs(ctx, limit)
}
