import type { SessionStatus } from "../types/api";

const currencyFormatter = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export function formatCurrency(value: number): string {
  return currencyFormatter.format(value);
}

// "HH:MM:SS" no fuso local.
export function formatTime(date: Date): string {
  return `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

// "DD/MM/AAAA HH:MM:SS" no fuso local.
export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const day = `${pad2(date.getDate())}/${pad2(date.getMonth() + 1)}/${date.getFullYear()}`;
  return `${day} ${formatTime(date)}`;
}

// "2h 05min", "12min" ou "< 1min".
export function formatDuration(fromIso: string, now: Date = new Date()): string {
  const minutes = Math.floor((now.getTime() - new Date(fromIso).getTime()) / 60_000);
  if (!(minutes >= 1)) return "< 1min";
  const hours = Math.floor(minutes / 60);
  if (hours === 0) return `${minutes}min`;
  return `${hours}h ${pad2(minutes % 60)}min`;
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}

export function normalizePlate(value: string): string {
  return value.toUpperCase().replace(/[^A-Z0-9]/g, "");
}

const statusLabels: Record<SessionStatus, string> = {
  PROCESSING: "Processando",
  PARKED: "Estacionado",
  PAID: "Pago",
  FAILED: "Falha no OCR",
};

export function statusLabel(status: SessionStatus): string {
  return statusLabels[status];
}

const actionLabels: Record<string, string> = {
  ENTRY: "Entrada",
  OCR_PROCESSING: "Leitura de placa",
  EXIT_PAYMENT: "Pagamento/Saída",
  POISON_MESSAGE: "Falha no processamento",
  OCR_FAILED: "Falha no OCR",
  ENTRY_FAILED: "Falha na entrada",
};

export function actionLabel(action: string): string {
  return Object.hasOwn(actionLabels, action) ? actionLabels[action] : action;
}
