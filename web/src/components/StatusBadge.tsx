import type { SessionStatus } from "../types/api";
import { statusLabel } from "../utils/format";

const statusClasses: Record<SessionStatus, string> = {
  PROCESSING: "bg-amber-100 text-amber-800",
  PARKED: "bg-blue-100 text-blue-800",
  PAID: "bg-green-100 text-green-800",
  FAILED: "bg-red-100 text-red-800",
};

export default function StatusBadge({ status }: { status: SessionStatus }) {
  return (
    <span
      className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${statusClasses[status]}`}
    >
      {statusLabel(status)}
    </span>
  );
}
