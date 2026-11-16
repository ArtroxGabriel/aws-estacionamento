import type { PlateStatus, SessionStatus } from "../types/api";
import { plateStatusLabel, statusLabel } from "../utils/format";

const badgeBase = "inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold";

const statusClasses: Record<SessionStatus, string> = {
  PARKED: "bg-blue-100 text-blue-800",
  PAID: "bg-green-100 text-green-800",
};

export default function StatusBadge({ status }: { status: SessionStatus }) {
  return <span className={`${badgeBase} ${statusClasses[status]}`}>{statusLabel(status)}</span>;
}

// Nenhuma cor de erro: sessão sem placa não é falha, o ticket vale do mesmo jeito.
const plateStatusClasses: Record<PlateStatus, string> = {
  PENDING: "bg-amber-100 text-amber-800",
  READ: "bg-slate-100 text-slate-700",
  UNREADABLE: "bg-slate-200 text-slate-700",
  MANUAL: "bg-purple-100 text-purple-800",
};

export function PlateStatusBadge({ status }: { status: PlateStatus }) {
  return (
    <span className={`${badgeBase} ${plateStatusClasses[status]}`}>{plateStatusLabel(status)}</span>
  );
}
