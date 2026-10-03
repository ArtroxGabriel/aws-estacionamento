import { Link } from "react-router";
import Alert from "../components/Alert";
import SpotsCounter from "../components/SpotsCounter";
import { usePolling } from "../hooks/usePolling";
import { errorMessage, getAvailableSpots } from "../services/api";

const shortcuts = [
  { to: "/entrada", title: "Simular entrada", description: "Enviar a foto do veículo e emitir o ticket." },
  { to: "/saida", title: "Processar pagamento", description: "Cobrar a permanência e liberar a vaga." },
  { to: "/auditoria", title: "Auditoria", description: "Consultar a trilha de eventos do sistema." },
];

export default function DashboardPage() {
  const { data, error, loading, lastUpdated, refresh } = usePolling(getAvailableSpots, 5000);

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

      <nav aria-label="Atalhos" className="grid gap-4 sm:grid-cols-3">
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
    </div>
  );
}
