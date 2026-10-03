export type SessionStatus = "PROCESSING" | "PARKED" | "PAID" | "FAILED";

// GET /sessions?status=ALL ignora o filtro de status.
export type SessionStatusFilter = SessionStatus | "ALL";

export interface Session {
  id: string;
  license_plate?: string;
  status: SessionStatus;
  s3_photo_key: string;
  entered_at: string; // ISO 8601 (UTC)
  exited_at?: string;
  amount_paid?: number;
}

export interface AvailableSpotsResponse {
  available_spots: number;
}

export type AuditAction =
  | "ENTRY"
  | "OCR_PROCESSING"
  | "EXIT_PAYMENT"
  | "OCR_FAILED" // worker: placa não identificada, sessão marcada FAILED
  | "POISON_MESSAGE" // worker: mensagem falhou na última entrega do SQS
  | "ENTRY_FAILED" // API: falha ao publicar a entrada no SQS
  | "PLATE_CORRECTION" // API: placa digitada/corrigida no caixa (FAILED vira PARKED)
  | "SESSION_DELETE"; // API: sessão excluída (e a foto removida do S3)

export interface AuditEvent {
  id: string; // "<session_id>#<timestamp_nano>"
  action: AuditAction | (string & {}); // aceita ações futuras sem quebrar
  entity_id: string; // session_id
  timestamp: string; // RFC3339Nano
  details: Record<string, unknown>; // ENTRY: status, s3_photo_key | OCR_PROCESSING: license_plate
  // EXIT_PAYMENT: status, amount_paid | OCR_FAILED: status, reason
  // POISON_MESSAGE: reason | ENTRY_FAILED: status, error
  // PLATE_CORRECTION: license_plate, previous_plate, previous_status, status
  // SESSION_DELETE: status, license_plate, s3_photo_key
}
