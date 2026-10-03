import type { AuditEvent, Session, SessionStatus } from "../types/api";
import { formatCurrency, statusLabel } from "./format";

const SESSION_STATUSES: readonly string[] = ["PROCESSING", "PARKED", "PAID", "FAILED"];

function isSessionStatus(value: string): value is SessionStatus {
  return SESSION_STATUSES.includes(value);
}

// `details` vem do DynamoDB sem garantia de formato: cada campo é validado com typeof.
export function plateOf(details: Record<string, unknown>): string | undefined {
  const plate = details.license_plate;
  return typeof plate === "string" && plate !== "" ? plate : undefined;
}

export interface SessionInfo {
  plate?: string;
  status?: SessionStatus; // status atual no RDS, quando conhecido
}

// Situação atual de cada sessão (entity_id → placa/status). Os eventos são imutáveis:
// ENTRY guarda o status do momento da entrada e EXIT_PAYMENT não traz a placa. As sessões
// do RDS dão o estado atual; os eventos de OCR/falha cobrem o que não estiver nelas.
export function sessionInfo(
  events: readonly AuditEvent[],
  sessions: readonly Session[],
): Map<string, SessionInfo> {
  const info = new Map<string, SessionInfo>();
  for (const s of sessions) {
    info.set(s.id, { plate: s.license_plate || undefined, status: s.status });
  }
  for (const e of events) {
    const current = info.get(e.entity_id) ?? {};
    const plate = plateOf(e.details);
    if (plate) current.plate ??= plate;
    if (e.action === "POISON_MESSAGE") current.status ??= "FAILED";
    info.set(e.entity_id, current);
  }
  return info;
}

// Resultado do OCR da sessão, exibido no evento de entrada em vez do status gravado
// naquele momento (sempre "Processando").
function entryOutcome(info: SessionInfo | undefined): string | undefined {
  if (info?.plate) return "Processado";
  if (info?.status === undefined || info.status === "PROCESSING") return undefined;
  return statusLabel("FAILED"); // concluiu sem placa: falha no OCR (mesmo que já pago)
}

export function describeEvent(event: AuditEvent, info: SessionInfo | undefined): string {
  if (event.action === "ENTRY") {
    const outcome = entryOutcome(info);
    if (outcome) return outcome;
  }
  return describeDetails(event.details);
}

export function describeDetails(details: Record<string, unknown>): string {
  const parts: string[] = [];
  const { status, amount_paid: amountPaid, reason } = details;
  if (typeof status === "string" && status !== "") {
    parts.push(isSessionStatus(status) ? statusLabel(status) : status);
  }
  if (typeof amountPaid === "number") parts.push(formatCurrency(amountPaid));
  if (typeof reason === "string" && reason !== "") parts.push(reason);
  return parts.join(" · ");
}

// Mais recente primeiro. Date.parse ignora os nanossegundos; o desempate usa o texto.
export function sortByNewest(events: readonly AuditEvent[]): AuditEvent[] {
  return [...events].sort(
    (a, b) =>
      (Date.parse(b.timestamp) || 0) - (Date.parse(a.timestamp) || 0) ||
      b.timestamp.localeCompare(a.timestamp),
  );
}
