package handler

import (
	"encoding/json"
	"errors"
	"net/http"

	"api/internal/service"
)

// maxUploadBytes caps the whole POST /entries request body.
const maxUploadBytes = 10 << 20

type Handler struct {
	svc *service.ParkingService
}

func NewHandler(svc *service.ParkingService) *Handler {
	return &Handler{svc: svc}
}

func NewServeMux(h *Handler) *http.ServeMux {
	mux := http.NewServeMux()
	h.RegisterRoutes(mux)
	return mux
}

// WithCORS allows any origin. The API is deliberately open (no auth, see
// docs/GOAL.md), so this lets the Vite dev server call it from another origin.
func WithCORS(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (h *Handler) RegisterRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /health", h.HandleHealth)
	mux.HandleFunc("GET /spots/available", h.HandleGetAvailableSpots)
	mux.HandleFunc("POST /entries", h.HandleCreateEntry)
	mux.HandleFunc("POST /exits/{id}/pay", h.HandlePayExit)
	mux.HandleFunc("GET /sessions", h.HandleGetSessions)
	mux.HandleFunc("GET /audit", h.HandleGetAuditLogs)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// writeError always emits valid JSON, whatever characters the message holds.
func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

func (h *Handler) HandleHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "UP"})
}

func (h *Handler) HandleGetAvailableSpots(w http.ResponseWriter, r *http.Request) {
	spots, err := h.svc.GetAvailableSpots(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"available_spots": spots})
}

func (h *Handler) HandleCreateEntry(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxUploadBytes)
	if err := r.ParseMultipartForm(maxUploadBytes); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			writeError(w, http.StatusRequestEntityTooLarge, "photo too large")
			return
		}
		writeError(w, http.StatusBadRequest, "invalid multipart form")
		return
	}

	file, header, err := r.FormFile("photo")
	if err != nil {
		writeError(w, http.StatusBadRequest, "photo is required")
		return
	}
	defer file.Close()

	session, err := h.svc.CreateEntry(r.Context(), header.Filename, file, header.Header.Get("Content-Type"))
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}

	writeJSON(w, http.StatusCreated, session)
}

func (h *Handler) HandlePayExit(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if id == "" {
		writeError(w, http.StatusBadRequest, "missing session id")
		return
	}

	paidSession, err := h.svc.PayExit(r.Context(), id)
	if err != nil {
		if errors.Is(err, service.ErrSessionNotFound) {
			writeError(w, http.StatusNotFound, "session not found")
			return
		}
		if errors.Is(err, service.ErrInvalidSessionStatus) {
			writeError(w, http.StatusConflict, "session is not in a payable status")
			return
		}
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}

	writeJSON(w, http.StatusOK, paidSession)
}

func (h *Handler) HandleGetSessions(w http.ResponseWriter, r *http.Request) {
	plate := r.URL.Query().Get("plate")
	var platePtr *string
	if plate != "" {
		platePtr = &plate
	}

	status := r.URL.Query().Get("status")
	if status == "" {
		status = "PARKED" // Default to parked
	}
	var statusPtr *string
	if status != "ALL" {
		statusPtr = &status
	}

	sessions, err := h.svc.FindSessions(r.Context(), statusPtr, platePtr)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}

	writeJSON(w, http.StatusOK, sessions)
}

func (h *Handler) HandleGetAuditLogs(w http.ResponseWriter, r *http.Request) {
	logs, err := h.svc.GetAuditLogs(r.Context(), 50)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}

	writeJSON(w, http.StatusOK, logs)
}
