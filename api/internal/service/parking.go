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
	"time"
)

var (
	ErrSessionNotFound = errors.New("session not found")
	ErrInvalidPhoto    = errors.New("photo is required")
)

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
	return s.spotsRepo.GetAvailable(ctx, s.totalSpots)
}

func (s *ParkingService) CreateEntry(ctx context.Context, photoFileName string, photoBody io.Reader, contentType string) (*model.Session, error) {
	if photoBody == nil {
		return nil, ErrInvalidPhoto
	}

	sessionID := generateID()
	s3Key := fmt.Sprintf("photos/%s_%s", sessionID, photoFileName)

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

	_ = s.publisher.Publish(ctx, map[string]string{
		"session_id": sessionID,
		"s3_key":     s3Key,
	})

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

	paidSession, err := s.sessionRepo.MarkAsPaid(ctx, sessionID, time.Now().UTC(), s.fixedRate)
	if err != nil {
		return nil, fmt.Errorf("failed to update session: %w", err)
	}

	_, _ = s.spotsRepo.Increment(ctx)

	_ = s.audit.LogEvent(ctx, "EXIT_PAYMENT", sessionID, map[string]any{
		"amount_paid": s.fixedRate,
		"status":      paidSession.Status,
	})

	return paidSession, nil
}

func generateID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}
