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
	ErrSessionNotEditable   = errors.New("session plate can only be set while PARKED or FAILED")
)

// Mercosul (ABC1D23) or old format (ABC1234), after removing spaces/hyphens.
var platePattern = regexp.MustCompile(`^[A-Z]{3}[0-9][A-Z0-9][0-9]{2}$`)
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
	fixedRate   float64
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
		fixedRate:   cfg.FixedParkingRate,
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

func (s *ParkingService) CreateEntry(ctx context.Context, photoFileName string, photoBody io.Reader, contentType string) (*model.Session, error) {
	if photoBody == nil {
		return nil, ErrInvalidPhoto
	}

	sessionID := generateID()
	s3Key := fmt.Sprintf("photos/%s_%s", sessionID, safeFileName(photoFileName))

	if err := s.storage.Upload(ctx, s3Key, photoBody, contentType); err != nil {
		return nil, fmt.Errorf("failed to upload photo: %w", err)
	}

	session := &model.Session{
		ID:         sessionID,
		Status:     "PROCESSING",
		S3PhotoKey: s3Key,
		EnteredAt:  time.Now().UTC(),
	}

	if err := s.sessionRepo.Create(ctx, session); err != nil {
		return nil, fmt.Errorf("failed to create session: %w", err)
	}

	if err := s.publisher.Publish(ctx, map[string]string{
		"session_id": sessionID,
		"s3_key":     s3Key,
	}); err != nil {
		_ = s.sessionRepo.MarkAsFailed(ctx, sessionID)
		_ = s.audit.LogEvent(ctx, "ENTRY_FAILED", sessionID, map[string]any{
			"s3_photo_key": s3Key,
			"status":       "FAILED",
			"error":        err.Error(),
		})
		return nil, fmt.Errorf("failed to publish entry event: %w", err)
	}

	_ = s.audit.LogEvent(ctx, "ENTRY", sessionID, map[string]any{
		"s3_photo_key": s3Key,
		"status":       session.Status,
	})

	return session, nil
}

func (s *ParkingService) PayExit(ctx context.Context, sessionID string) (*model.Session, error) {
	session, err := s.sessionRepo.GetByID(ctx, sessionID)
	if err != nil {
		return nil, fmt.Errorf("failed to retrieve session: %w", err)
	}
	if session == nil {
		return nil, ErrSessionNotFound
	}
	if session.Status != "PARKED" && session.Status != "FAILED" {
		return nil, ErrInvalidSessionStatus
	}

	previousStatus := session.Status
	paidSession, err := s.sessionRepo.MarkAsPaid(ctx, sessionID, time.Now().UTC(), s.fixedRate)
	if err != nil {
		return nil, fmt.Errorf("failed to update session: %w", err)
	}
	if paidSession == nil {
		return nil, ErrInvalidSessionStatus
	}

	if previousStatus == "PARKED" {
		_, _ = s.spotsRepo.Increment(ctx)
	}

	_ = s.audit.LogEvent(ctx, "EXIT_PAYMENT", sessionID, map[string]any{
		"amount_paid": s.fixedRate,
		"status":      paidSession.Status,
	})

	return paidSession, nil
}

// UpdatePlate lets the cashier type the plate when OCR failed (FAILED ->
// PARKED, which now takes a spot) or fix a misread one (PARKED stays PARKED).
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
	if session.Status != "PARKED" && session.Status != "FAILED" {
		return nil, ErrSessionNotEditable
	}

	previousStatus := session.Status
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

	// A FAILED session never took a spot (the worker only decrements on a
	// readable plate); now that it is PARKED it must.
	if previousStatus == "FAILED" {
		_, _ = s.spotsRepo.Decrement(ctx)
	}

	_ = s.audit.LogEvent(ctx, "PLATE_CORRECTION", sessionID, map[string]any{
		"license_plate":   plate,
		"previous_plate":  previousPlate,
		"previous_status": previousStatus,
		"status":          updated.Status,
	})

	return updated, nil
}

// DeleteSession removes a session and its photo (the D of the CRUD). A PARKED
// session gives its spot back; the others never held one in the counter.
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

	if session.Status == "PARKED" {
		_, _ = s.spotsRepo.Increment(ctx)
	}
	// Best effort: an orphan photo costs cents, a failed delete must not
	// resurrect the session.
	if session.S3PhotoKey != "" {
		_ = s.storage.Delete(ctx, session.S3PhotoKey)
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

func (s *ParkingService) FindSessions(ctx context.Context, status *string, plate *string) ([]*model.Session, error) {
	return s.sessionRepo.FindAll(ctx, status, plate)
}

func (s *ParkingService) GetAuditLogs(ctx context.Context, limit int) ([]*model.AuditLog, error) {
	if limit <= 0 {
		limit = 50
	}
	return s.audit.GetRecentLogs(ctx, limit)
}
