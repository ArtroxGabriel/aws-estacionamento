package service_test

import (
	"context"
	"errors"
	"fmt"
	"testing"

	"api/internal/model"
	"api/internal/service"
)

// FakeAuditReader implements repository.AuditReader
type FakeAuditReader struct {
	events    []model.AuditEvent
	shouldErr bool
	calls     int
}

func (f *FakeAuditReader) ListEvents(ctx context.Context) ([]model.AuditEvent, error) {
	f.calls++
	if f.shouldErr {
		return nil, errors.New("dynamodb error")
	}
	return f.events, nil
}

func TestAuditListEvents_SortsByTimestampDesc(t *testing.T) {
	reader := &FakeAuditReader{events: []model.AuditEvent{
		{ID: "a", Timestamp: "2026-10-03T14:05:09.5Z"},
		// Lexically "...09.5Z" > "...09.51Z" ('Z' > '1'), but .51s is later than .5s: parsing must win.
		{ID: "c", Timestamp: "2026-10-03T14:05:09.51Z"},
		{ID: "b", Timestamp: "2026-10-03T14:05:09.123456789Z"},
		{ID: "d", Timestamp: "2026-10-03T14:05:10Z"},
		{ID: "oldest", Timestamp: "2026-10-02T23:59:59.999Z"},
	}}
	svc := service.NewAuditService(reader)

	events, err := svc.ListEvents(context.Background(), 100)
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}

	want := []string{"d", "c", "a", "b", "oldest"}
	if len(events) != len(want) {
		t.Fatalf("expected %d events, got %d", len(want), len(events))
	}
	for i, id := range want {
		if events[i].ID != id {
			t.Fatalf("position %d: expected %s, got %s (all: %+v)", i, id, events[i].ID, events)
		}
	}
}

func TestAuditListEvents_UnparseableTimestampFallsBackToString(t *testing.T) {
	reader := &FakeAuditReader{events: []model.AuditEvent{
		{ID: "x", Timestamp: "aaa"},
		{ID: "y", Timestamp: "bbb"},
	}}
	svc := service.NewAuditService(reader)

	events, err := svc.ListEvents(context.Background(), 10)
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
	if events[0].ID != "y" || events[1].ID != "x" {
		t.Fatalf("expected string-desc fallback [y x], got %+v", events)
	}
}

func TestAuditListEvents_Truncates(t *testing.T) {
	var all []model.AuditEvent
	for i := 0; i < 10; i++ {
		all = append(all, model.AuditEvent{
			ID:        fmt.Sprintf("e%d", i),
			Timestamp: fmt.Sprintf("2026-10-03T14:05:%02dZ", i),
		})
	}
	svc := service.NewAuditService(&FakeAuditReader{events: all})

	events, err := svc.ListEvents(context.Background(), 3)
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
	if len(events) != 3 {
		t.Fatalf("expected 3 events, got %d", len(events))
	}
	if events[0].ID != "e9" || events[1].ID != "e8" || events[2].ID != "e7" {
		t.Fatalf("expected the 3 newest events [e9 e8 e7], got %+v", events)
	}
}

func TestAuditListEvents_LimitBounds(t *testing.T) {
	for _, limit := range []int{0, -1, 501, 1000} {
		reader := &FakeAuditReader{}
		svc := service.NewAuditService(reader)
		_, err := svc.ListEvents(context.Background(), limit)
		if !errors.Is(err, service.ErrInvalidLimit) {
			t.Fatalf("limit %d: expected ErrInvalidLimit, got %v", limit, err)
		}
		if reader.calls != 0 {
			t.Fatalf("limit %d: expected reader not to be called", limit)
		}
	}

	for _, limit := range []int{1, 500} {
		svc := service.NewAuditService(&FakeAuditReader{})
		if _, err := svc.ListEvents(context.Background(), limit); err != nil {
			t.Fatalf("limit %d: expected no error, got %v", limit, err)
		}
	}
}

func TestAuditListEvents_EmptyIsNonNil(t *testing.T) {
	svc := service.NewAuditService(&FakeAuditReader{events: nil})

	events, err := svc.ListEvents(context.Background(), 100)
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
	if events == nil || len(events) != 0 {
		t.Fatalf("expected non-nil empty slice, got %#v", events)
	}
}

func TestAuditListEvents_ReaderError(t *testing.T) {
	svc := service.NewAuditService(&FakeAuditReader{shouldErr: true})

	_, err := svc.ListEvents(context.Background(), 100)
	if err == nil || errors.Is(err, service.ErrInvalidLimit) {
		t.Fatalf("expected reader error, got %v", err)
	}
}
