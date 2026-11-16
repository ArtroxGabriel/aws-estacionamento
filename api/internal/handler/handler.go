package handler

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"

	"api/internal/repository"
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
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
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
	mux.HandleFunc("GET /sessions/{id}/photo", h.HandleGetSessionPhoto)
	mux.HandleFunc("PATCH /sessions/{id}", h.HandleUpdatePlate)
	mux.HandleFunc("DELETE /sessions/{id}", h.HandleDeleteSession)
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
	if errors.Is(err, service.ErrParkingFull) {
		writeError(w, http.StatusConflict, "parking lot is full")
		return
	}
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

// HandleUpdatePlate receives {"license_plate": "..."} typed by the cashier.
func (h *Handler) HandleUpdatePlate(w http.ResponseWriter, r *http.Request) {
	var body struct {
		LicensePlate string `json:"license_plate"`
	}
	r.Body = http.MaxBytesReader(w, r.Body, 1<<10)
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}

	session, err := h.svc.UpdatePlate(r.Context(), r.PathValue("id"), body.LicensePlate)
	switch {
	case errors.Is(err, service.ErrInvalidPlate):
		writeError(w, http.StatusBadRequest, "invalid license plate (expected ABC1D23, ABC1234, AB123CD, ABC123 or ABCD123)")
	case errors.Is(err, service.ErrSessionNotFound):
		writeError(w, http.StatusNotFound, "session not found")
	case errors.Is(err, service.ErrSessionNotEditable):
		writeError(w, http.StatusConflict, err.Error())
	case err != nil:
		writeError(w, http.StatusInternalServerError, err.Error())
	default:
		writeJSON(w, http.StatusOK, session)
	}
}

func (h *Handler) HandleDeleteSession(w http.ResponseWriter, r *http.Request) {
	session, err := h.svc.DeleteSession(r.Context(), r.PathValue("id"))
	switch {
	case errors.Is(err, service.ErrSessionNotFound):
		writeError(w, http.StatusNotFound, "session not found")
	case err != nil:
		writeError(w, http.StatusInternalServerError, err.Error())
	default:
		writeJSON(w, http.StatusOK, session)
	}
}

func (h *Handler) HandleGetSessions(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query()
	filter := repository.SessionFilter{
		Status:      query.Get("status"),
		Plate:       query.Get("plate"),
		PlateStatus: query.Get("plate_status"),
	}
	switch filter.Status {
	case "":
		filter.Status = "PARKED" // Default to parked
	case "ALL":
		filter.Status = ""
	}

	sessions, err := h.svc.FindSessions(r.Context(), filter)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}

	writeJSON(w, http.StatusOK, sessions)
}

// HandleGetSessionPhoto streams the entry photo of a session from the object
// storage. It is evidence for the cashier and the audit trail, so it is never
// cached by shared caches.
func (h *Handler) HandleGetSessionPhoto(w http.ResponseWriter, r *http.Request) {
	body, contentType, err := h.svc.GetSessionPhoto(r.Context(), r.PathValue("id"))
	switch {
	case errors.Is(err, service.ErrSessionNotFound):
		writeError(w, http.StatusNotFound, "session not found")
		return
	case errors.Is(err, service.ErrPhotoNotFound):
		writeError(w, http.StatusNotFound, "photo not found")
		return
	case err != nil:
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	defer body.Close()

	w.Header().Set("Content-Type", contentType)
	// The stored type came from the uploader: never let the browser guess.
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Cache-Control", "private, max-age=300")
	if contentType == "application/octet-stream" {
		w.Header().Set("Content-Disposition", "attachment")
	}
	w.WriteHeader(http.StatusOK)
	_, _ = io.Copy(w, body)
}

func (h *Handler) HandleGetAuditLogs(w http.ResponseWriter, r *http.Request) {
	logs, err := h.svc.GetAuditLogs(r.Context(), 50)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}

	writeJSON(w, http.StatusOK, logs)
}
