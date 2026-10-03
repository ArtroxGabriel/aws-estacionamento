import type {
  ActiveSession,
  AuditEvent,
  AuditListResponse,
  AvailableSpotsResponse,
  Session,
  SessionListResponse,
  SessionStatus,
} from "../types/api";

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "/api").replace(/\/$/, "");

export const NETWORK_ERROR_MESSAGE = "Não foi possível conectar à API.";

export class ApiError extends Error {
  status: number; // 0 = falha de rede
  message: string;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.message = message;
  }
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

// A API responde erros com `{"error":"..."}`, mas com Content-Type text/plain (http.Error),
// por isso o corpo é sempre lido como texto e o JSON é tentado sem confiar no header.
function errorFromBody(body: unknown): string | undefined {
  if (typeof body === "object" && body !== null && "error" in body) {
    const { error } = body;
    if (typeof error === "string" && error !== "") return error;
  }
  return undefined;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, init);
  } catch (err) {
    if (err instanceof TypeError) throw new ApiError(0, NETWORK_ERROR_MESSAGE);
    throw err;
  }

  const text = await res.text();
  const body = parseJson(text);

  if (!res.ok) {
    const message = errorFromBody(body) ?? (text.trim() || res.statusText);
    throw new ApiError(res.status, message);
  }
  if (body === undefined) {
    throw new ApiError(res.status, "Resposta inválida da API.");
  }
  return body as T;
}

export async function getAvailableSpots(): Promise<number> {
  const data = await request<AvailableSpotsResponse>("/spots/available");
  return data.available_spots;
}

export function createEntry(photo: File): Promise<Session> {
  const form = new FormData();
  form.append("photo", photo);
  // Sem Content-Type manual: o navegador define o boundary do multipart.
  return request<Session>("/entries", { method: "POST", body: form });
}

export function getSession(id: string): Promise<Session> {
  return request<Session>(`/sessions/${encodeURIComponent(id)}`);
}

export async function listSessions(status: SessionStatus = "PARKED"): Promise<ActiveSession[]> {
  const data = await request<SessionListResponse>(`/sessions?status=${encodeURIComponent(status)}`);
  return data.sessions;
}

export function payExit(id: string): Promise<Session> {
  return request<Session>(`/exits/${encodeURIComponent(id)}/pay`, { method: "POST" });
}

export async function listAuditEvents(limit = 100): Promise<AuditEvent[]> {
  const data = await request<AuditListResponse>(`/audit?limit=${limit}`);
  return data.events;
}

// Converte um erro em mensagem para a interface (seção 3.3 de docs/frontend.md).
export function errorMessage(err: unknown, context: "session" | "resource" = "resource"): string {
  if (!(err instanceof ApiError)) return "Erro inesperado. Tente novamente.";
  if (err.status === 0) return NETWORK_ERROR_MESSAGE;
  if (err.status === 404) {
    return context === "session" ? "Sessão não encontrada." : "Recurso não encontrado.";
  }
  if (err.status === 409) return "A sessão não está pronta para pagamento.";
  if (err.status >= 500) {
    const base = "Erro no servidor. Tente novamente.";
    return err.message ? `${base} (${err.message})` : base;
  }
  return err.message;
}
