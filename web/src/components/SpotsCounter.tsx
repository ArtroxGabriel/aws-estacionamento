import { formatTime } from "../utils/format";
import Spinner from "./Spinner";

interface SpotsCounterProps {
  value?: number;
  loading: boolean;
  stale: boolean; // erro com dado anterior: exibe o último valor conhecido
  lastUpdated?: Date;
}

function valueClass(value: number): string {
  if (value === 0) return "text-red-600";
  if (value <= 5) return "text-amber-600";
  return "text-green-600";
}

export default function SpotsCounter({ value, loading, stale, lastUpdated }: SpotsCounterProps) {
  return (
    <section
      aria-labelledby="spots-title"
      className="rounded-lg border border-slate-200 bg-white p-6 text-center shadow-sm"
    >
      <h2 id="spots-title" className="text-sm font-medium uppercase tracking-wide text-slate-500">
        Vagas disponíveis
      </h2>
      <div className="flex min-h-24 items-center justify-center" aria-live="polite">
        {value === undefined ? (
          loading ? (
            <Spinner className="size-10 text-slate-400" />
          ) : (
            <span className="text-6xl font-bold text-slate-400">—</span>
          )
        ) : value === 0 ? (
          <span className="text-6xl font-bold text-red-600">LOTADO</span>
        ) : (
          <span className={`text-7xl font-bold tabular-nums ${valueClass(value)}`}>{value}</span>
        )}
      </div>
      {stale && <p className="text-sm text-amber-700">Sem conexão — exibindo último valor</p>}
      {lastUpdated && (
        <p className="text-xs text-slate-500">Atualizado às {formatTime(lastUpdated)}</p>
      )}
    </section>
  );
}
