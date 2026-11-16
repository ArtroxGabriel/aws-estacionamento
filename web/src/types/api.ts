// Onde o carro está. O ticket (id da sessão) é o que identifica o carro: a sessão já
// nasce PARKED, com a vaga reservada, e a foto nunca bloqueia a saída.
export type SessionStatus = "PARKED" | "PAID";

// Como foi a leitura da placa na foto de entrada. É só evidência (auditoria) e um atalho
// para achar o ticket no caixa.
export type PlateStatus =
  | "PENDING" // aguardando o worker
  | "READ" // lida pelo OCR
  | "UNREADABLE" // o OCR não achou placa, ou a foto não chegou a ser lida
  | "MANUAL"; // digitada no caixa

// GET /sessions?status=ALL ignora o filtro de status.
export type SessionStatusFilter = SessionStatus | "ALL";

export interface Session {
  id: string;
  license_plate?: string;
  status: SessionStatus;
  plate_status: PlateStatus;
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
  | "OCR_FAILED" // worker: placa não identificada (plate_status UNREADABLE; o ticket segue válido)
  | "POISON_MESSAGE" // worker: mensagem falhou na última entrega da fila
  | "ENTRY_FAILED" // legado: a API não emite mais (fila fora do ar não derruba a entrada)
  | "PLATE_CORRECTION" // API: placa digitada/corrigida no caixa (plate_status MANUAL)
  | "SESSION_DELETE"; // API: sessão excluída (e a foto removida do S3)

export interface AuditEvent {
  id: string; // "<session_id>#<timestamp_nano>"
  action: AuditAction | (string & {}); // aceita ações futuras sem quebrar
  entity_id: string; // session_id
  timestamp: string; // RFC3339Nano
  details: Record<string, unknown>; // ENTRY: status, s3_photo_key, ocr_queued, queue_error
  // OCR_PROCESSING: license_plate | EXIT_PAYMENT: status, amount_paid
  // OCR_FAILED: plate_status, reason | POISON_MESSAGE: reason
  // PLATE_CORRECTION: license_plate, previous_plate, previous_plate_status, status
  // SESSION_DELETE: status, license_plate, s3_photo_key
}
