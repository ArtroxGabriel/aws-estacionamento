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

// Placa de cada sessão (entity_id → placa). Só o OCR_PROCESSING grava a placa no evento;
// as sessões do RDS cobrem eventos cujo OCR ficou fora da janela carregada.
export function sessionPlates(
  events: readonly AuditEvent[],
  sessions: readonly Session[],
): Map<string, string> {
  const plates = new Map<string, string>();
  for (const s of sessions) {
    if (s.license_plate) plates.set(s.id, s.license_plate);
  }
  for (const e of events) {
    const plate = plateOf(e.details);
    if (plate) plates.set(e.entity_id, plate);
  }
  return plates;
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
