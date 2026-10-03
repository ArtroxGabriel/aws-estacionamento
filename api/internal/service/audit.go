package service

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"time"

	"api/internal/model"
	"api/internal/repository"
)

const (
	MinAuditLimit = 1
	MaxAuditLimit = 500
)

var ErrInvalidLimit = errors.New("invalid limit")

type AuditService struct {
	reader repository.AuditReader
}

func NewAuditService(reader repository.AuditReader) *AuditService {
	return &AuditService{reader: reader}
}

// ListEvents returns the most recent audit events (newest first), up to limit.
// DynamoDB Scan does not guarantee ordering, so all events are read and sorted here.
func (s *AuditService) ListEvents(ctx context.Context, limit int) ([]model.AuditEvent, error) {
	if limit < MinAuditLimit || limit > MaxAuditLimit {
		return nil, ErrInvalidLimit
	}

	events, err := s.reader.ListEvents(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to list audit events: %w", err)
	}

	sortEventsNewestFirst(events)

	if len(events) > limit {
		events = events[:limit]
	}
	if events == nil {
		events = []model.AuditEvent{}
	}
	return events, nil
}

// sortEventsNewestFirst orders events by timestamp desc. RFC3339Nano trims trailing
// zeros from the fraction, so lexical order is not chronological: timestamps are parsed,
// falling back to string comparison when either side cannot be parsed.
func sortEventsNewestFirst(events []model.AuditEvent) {
	type keyed struct {
		event  model.AuditEvent
		t      time.Time
		parsed bool
	}
	items := make([]keyed, len(events))
	for i, e := range events {
		t, err := time.Parse(time.RFC3339Nano, e.Timestamp)
		items[i] = keyed{event: e, t: t, parsed: err == nil}
	}

	sort.SliceStable(items, func(a, b int) bool {
		if items[a].parsed && items[b].parsed {
			return items[a].t.After(items[b].t)
		}
		return items[a].event.Timestamp > items[b].event.Timestamp
	})

	for i := range items {
		events[i] = items[i].event
	}
}
