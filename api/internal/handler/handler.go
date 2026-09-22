package handler

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"

	"api/internal/service"
)

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

func (h *Handler) RegisterRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /health", h.HandleHealth)
	mux.HandleFunc("GET /spots/available", h.HandleGetAvailableSpots)
	mux.HandleFunc("POST /entries", h.HandleCreateEntry)
	mux.HandleFunc("POST /exits/{id}/pay", h.HandlePayExit)
}

func (h *Handler) HandleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "UP"})
}

func (h *Handler) HandleGetAvailableSpots(w http.ResponseWriter, r *http.Request) {
	spots, err := h.svc.GetAvailableSpots(r.Context())
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":"%v"}`, err), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"available_spots": spots})
}

func (h *Handler) HandleCreateEntry(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseMultipartForm(10 << 20); err != nil {
		http.Error(w, `{"error":"invalid multipart form"}`, http.StatusBadRequest)
		return
	}

	file, header, err := r.FormFile("photo")
	if err != nil {
		http.Error(w, `{"error":"photo is required"}`, http.StatusBadRequest)
		return
	}
	defer file.Close()

	session, err := h.svc.CreateEntry(r.Context(), header.Filename, file, header.Header.Get("Content-Type"))
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":"%v"}`, err), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(session)
}

func (h *Handler) HandlePayExit(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if id == "" {
		http.Error(w, `{"error":"missing session id"}`, http.StatusBadRequest)
		return
	}

	paidSession, err := h.svc.PayExit(r.Context(), id)
	if err != nil {
		if errors.Is(err, service.ErrSessionNotFound) {
			http.Error(w, `{"error":"session not found"}`, http.StatusNotFound)
			return
		}
		http.Error(w, fmt.Sprintf(`{"error":"%v"}`, err), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(paidSession)
}
