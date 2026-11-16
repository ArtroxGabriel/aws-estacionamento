import type { AuditEvent, PlateStatus, Session, SessionStatus } from "../types/api";
import { formatCurrency, statusLabel } from "./format";

const SESSION_STATUSES: readonly string[] = ["PARKED", "PAID"];

function isSessionStatus(value: string): value is SessionStatus {
  return SESSION_STATUSES.includes(value);
}

// A trilha é imutável: eventos gravados antes de o ticket valer na entrada ainda trazem os
// status que não existem mais. Eles continuam legíveis.
const legacyStatusLabels: Record<string, string> = {
  PROCESSING: "Processando",
  FAILED: "Falha no OCR",
};

function anyStatusLabel(status: string): string {
  if (isSessionStatus(status)) return statusLabel(status);
  return Object.hasOwn(legacyStatusLabels, status) ? legacyStatusLabels[status] : status;
}

// Eventos de falha gravados pelo worker (OCR_FAILED, POISON_MESSAGE) e, no modelo antigo,
// pela API (ENTRY_FAILED). Falham a leitura da placa, nunca o ticket.
export const FAILURE_ACTIONS: readonly string[] = ["OCR_FAILED", "POISON_MESSAGE", "ENTRY_FAILED"];

export function isFailureAction(action: string): boolean {
  return FAILURE_ACTIONS.includes(action);
}

function stringField(details: Record<string, unknown>, key: string): string | undefined {
  const value = details[key];
  return typeof value === "string" && value !== "" ? value : undefined;
}

// `details` vem do DynamoDB sem garantia de formato: cada campo é validado com typeof.
export function plateOf(details: Record<string, unknown>): string | undefined {
  return stringField(details, "license_plate");
}

const s3Kinds: Record<string, string> = {
  not_found: "foto não encontrada no S3",
  too_large: "foto grande demais",
  invalid_params: "parâmetros inválidos no S3",
  unavailable: "S3 indisponível",
};

// Traduz o motivo de falha gravado pelo worker (worker/worker.py). Motivos desconhecidos
// aparecem como vieram.
export function failureReasonLabel(reason: string): string {
  const [head, ...rest] = reason.split(": ");
  const detail = rest.join(": ");
  if (head === "unreadable plate") {
    if (detail === "empty") return "Placa ilegível (nenhum texto reconhecido)";
    if (detail === "no_match") return "Placa ilegível (texto fora do padrão de placa)";
    return "Placa ilegível";
  }
  if (head === "OCR failed") return detail ? `Erro no OCR: ${detail}` : "Erro no OCR";
  if (head === "S3 download failed") {
    return `Falha ao baixar a foto${detail ? ` (${s3Kinds[detail] ?? detail})` : ""}`;
  }
  if (reason === "session lookup failed (RDS)") return "Falha ao consultar a sessão (RDS)";
  if (head.startsWith("unexpected session status")) {
    return `Status inesperado da sessão: ${head.replace("unexpected session status", "").trim()}`;
  }
  const step = /^(RDS|RDS commit|Redis|DynamoDB) failed$/.exec(reason);
  if (step) return `Falha ao gravar o resultado (${step[1]})`;
  if (reason === "processing failed") return "Falha no processamento";
  if (/JSON|invalid message|session_id|s3_key/.test(reason)) return "Mensagem inválida na fila";
  return reason;
}

export interface SessionInfo {
  plate?: string;
  status?: SessionStatus; // status atual no banco, quando a sessão ainda existe
  plateStatus?: PlateStatus; // como terminou a leitura da placa
  failure?: string; // motivo da falha mais recente registrada na auditoria
  hasPhoto?: boolean; // a sessão existe no banco, então a foto de entrada pode ser aberta
}

// Situação atual de cada sessão (entity_id → placa/status/falha). Os eventos são imutáveis:
// ENTRY é gravado antes de a placa ser lida e EXIT_PAYMENT não traz a placa. As sessões do
// banco dão o estado atual; os eventos de OCR/falha cobrem o que não estiver nelas (por
// exemplo, sessão já excluída). `events` deve vir do mais recente para o mais antigo.
export function sessionInfo(
  events: readonly AuditEvent[],
  sessions: readonly Session[],
): Map<string, SessionInfo> {
  const info = new Map<string, SessionInfo>();
  for (const s of sessions) {
    info.set(s.id, {
      plate: s.license_plate || undefined,
      status: s.status,
      plateStatus: s.plate_status,
      hasPhoto: true,
    });
  }
  for (const e of events) {
    const current = info.get(e.entity_id) ?? {};
    const plate = plateOf(e.details);
    if (plate) current.plate ??= plate;
    if (e.action === "OCR_PROCESSING") current.plateStatus ??= "READ";
    if (e.action === "OCR_FAILED") current.plateStatus ??= "UNREADABLE";
    if (e.action === "PLATE_CORRECTION") current.plateStatus ??= "MANUAL";
    if (isFailureAction(e.action)) {
      const reason = stringField(e.details, "reason") ?? stringField(e.details, "error");
      if (reason) current.failure ??= failureReasonLabel(reason);
    }
    info.set(e.entity_id, current);
  }
  return info;
}

// O que a entrada produziu. O carro sempre entra (Estacionado, vaga reservada na hora); o
// que varia é só o destino da foto, mostrado ao lado quando não houve placa.
function entryOutcome(event: AuditEvent, info: SessionInfo | undefined): string {
  const parked = statusLabel("PARKED");
  if (event.details.ocr_queued === false) {
    return `${parked} · foto não enviada para leitura (fila indisponível)`;
  }
  if (info?.plate) return parked;
  if (info?.failure) return `${parked} · sem placa: ${info.failure}`;
  if (info?.plateStatus === "UNREADABLE") return `${parked} · sem placa`;
  if (info?.plateStatus === "PENDING") return `${parked} · lendo placa`;
  return parked;
}

export function describeEvent(event: AuditEvent, info: SessionInfo | undefined): string {
  if (event.action === "PLATE_CORRECTION") {
    const plate = plateOf(event.details) ?? "—";
    const previous = stringField(event.details, "previous_plate");
    return previous ? `${plate} (antes: ${previous})` : `${plate} (digitada no caixa)`;
  }
  if (event.action === "SESSION_DELETE") {
    const status = stringField(event.details, "status");
    return status ? `Registro excluído · estava ${anyStatusLabel(status)}` : "Registro excluído";
  }
  if (event.action === "ENTRY") return entryOutcome(event, info);
  if (isFailureAction(event.action)) {
    // O rótulo da ação já diz que houve falha: os detalhes mostram só o motivo.
    const reason = stringField(event.details, "reason") ?? stringField(event.details, "error");
    return reason ? failureReasonLabel(reason) : "";
  }
  return describeDetails(event.details);
}

export function describeDetails(details: Record<string, unknown>): string {
  const parts: string[] = [];
  const { amount_paid: amountPaid } = details;
  const status = stringField(details, "status");
  const reason = stringField(details, "reason");
  if (status) parts.push(anyStatusLabel(status));
  if (typeof amountPaid === "number") parts.push(formatCurrency(amountPaid));
  if (reason) parts.push(failureReasonLabel(reason));
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
