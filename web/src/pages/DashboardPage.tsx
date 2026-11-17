import { Link } from "react-router";
import Alert from "../components/Alert";
import SpotsCounter from "../components/SpotsCounter";
import StatusBadge from "../components/StatusBadge";
import { usePolling } from "../hooks/usePolling";
import { errorMessage, getAvailableSpots, listSessions } from "../services/api";
import type { Session } from "../types/api";
import { formatDateTime, shortId } from "../utils/format";

const shortcuts = [
  { to: "/cancela-entrada", title: "Cancela de entrada", description: "Enviar a foto do veículo e emitir o ticket." },
  { to: "/pagamento", title: "Pagamento", description: "Cobrar a permanência no caixa." },
  { to: "/cancela-saida", title: "Cancela de saída", description: "Liberar a saída pela placa ou pelo ticket." },
  { to: "/auditoria", title: "Auditoria", description: "Consultar a trilha de eventos do sistema." },
];

// Carros no pátio: os não pagos (PARKED) e os pagos que ainda não passaram pela cancela
// (PAID). Os dois ocupam vaga.
async function listCarsInside(): Promise<Session[]> {
  const sessions = await listSessions("ALL");
  return sessions
    .filter((s) => s.status === "PARKED" || s.status === "PAID")
    .sort((a, b) => Date.parse(a.entered_at) - Date.parse(b.entered_at));
}

export default function DashboardPage() {
  const { data, error, loading, lastUpdated, refresh } = usePolling(getAvailableSpots, 5000);
  const cars = usePolling(listCarsInside, 15_000);
  const paidCount = cars.data?.filter((s) => s.status === "PAID").length ?? 0;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Painel</h1>

      {error && data === undefined ? (
        <Alert variant="error" onRetry={refresh}>
          {errorMessage(error)}
        </Alert>
      ) : (
        <SpotsCounter
          value={data}
          loading={loading}
          stale={error !== undefined}
          lastUpdated={lastUpdated}
        />
      )}

      <nav aria-label="Atalhos" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {shortcuts.map((s) => (
          <Link
            key={s.to}
            to={s.to}
            className="block rounded-lg border border-slate-200 bg-white p-4 shadow-sm hover:border-blue-400"
          >
            <span className="block font-semibold text-slate-900">{s.title}</span>
            <span className="mt-1 block text-sm text-slate-600">{s.description}</span>
          </Link>
        ))}
      </nav>

      <section aria-labelledby="cars-inside" className="space-y-3">
        <h2 id="cars-inside" className="text-lg font-semibold">
          Veículos no pátio
        </h2>
        {cars.error && cars.data === undefined ? (
          <Alert variant="error" onRetry={cars.refresh}>
            {errorMessage(cars.error)}
          </Alert>
        ) : cars.data === undefined ? null : cars.data.length === 0 ? (
          <p className="text-slate-600">Nenhum veículo no pátio.</p>
        ) : (
          <>
            <p className="text-sm text-slate-600">
              {cars.data.length} no pátio · {paidCount} já pago{paidCount === 1 ? "" : "s"},
              aguardando a cancela de saída.
            </p>
            <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-100 text-slate-600">
                  <tr>
                    <th scope="col" className="px-3 py-2">Ticket</th>
                    <th scope="col" className="px-3 py-2">Placa</th>
                    <th scope="col" className="px-3 py-2">Entrada</th>
                    <th scope="col" className="px-3 py-2">Situação</th>
                  </tr>
                </thead>
                <tbody>
                  {cars.data.map((s) => (
                    <tr key={s.id} className="border-t border-slate-200">
                      <td className="px-3 py-2 font-mono" title={s.id}>{shortId(s.id)}</td>
                      <td className="px-3 py-2 font-mono">{s.license_plate ?? "—"}</td>
                      <td className="px-3 py-2 whitespace-nowrap">{formatDateTime(s.entered_at)}</td>
                      <td className="px-3 py-2">
                        <StatusBadge status={s.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
