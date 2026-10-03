export type SessionStatus = "PROCESSING" | "PARKED" | "PAID" | "FAILED";

export interface Session {
  id: string;
  license_plate?: string;
  status: SessionStatus;
  s3_photo_key: string;
  entered_at: string; // ISO 8601 (UTC)
  exited_at?: string;
  amount_paid?: number;
}

// Item de GET /sessions: sessão + valor a pagar calculado pela API.
export interface ActiveSession extends Session {
  amount_due: number;
}

export interface AvailableSpotsResponse {
  available_spots: number;
}

export interface SessionListResponse {
  sessions: ActiveSession[];
}

export type AuditAction = "ENTRY" | "OCR_PROCESSING" | "EXIT_PAYMENT" | "POISON_MESSAGE";

export interface AuditEvent {
  id: string; // "<session_id>#<timestamp_nano>"
  action: AuditAction | (string & {}); // aceita ações futuras sem quebrar
  entity_id: string; // session_id
  timestamp: string; // RFC3339Nano
  details: Record<string, unknown>; // ENTRY: status, s3_photo_key | OCR_PROCESSING: license_plate
  // EXIT_PAYMENT: status, amount_paid | POISON_MESSAGE: reason
}

export interface AuditListResponse {
  events: AuditEvent[];
}
