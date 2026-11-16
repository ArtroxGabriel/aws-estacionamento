import type { Session } from "../types/api";
import { formatDateTime, shortId } from "../utils/format";
import StatusBadge from "./StatusBadge";

// O ticket vale a partir da emissão: é ele (e não a placa) que identifica o carro na saída.
export default function TicketCard({ session }: { session: Session }) {
  return (
    <section
      aria-labelledby="ticket-title"
      className="space-y-3 rounded-lg border-2 border-dashed border-slate-300 bg-white p-6 text-center"
    >
      <h2 id="ticket-title" className="text-sm font-medium uppercase tracking-wide text-slate-500">
        Ticket de estacionamento
      </h2>
      <p className="font-mono text-4xl font-bold tracking-wider">{shortId(session.id)}</p>
      <p className="select-all break-all font-mono text-xs text-slate-500">{session.id}</p>
      <p className="text-sm text-slate-700">Entrada: {formatDateTime(session.entered_at)}</p>
      <StatusBadge status={session.status} />
      <p className="text-sm text-slate-700">
        Cancela liberada. Guarde este ticket: é com ele que você paga a saída.
      </p>
      {session.plate_status === "PENDING" && (
        <p className="text-sm text-slate-500">A placa está sendo identificada pela foto...</p>
      )}
    </section>
  );
}
