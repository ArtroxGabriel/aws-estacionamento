import { useCallback, useEffect, useMemo, useState } from "react";
import Alert from "../components/Alert";
import Spinner from "../components/Spinner";
import { errorMessage, listAuditEvents } from "../services/api";
import type { AuditAction, AuditEvent } from "../types/api";
import { describeDetails, plateOf, sortByNewest } from "../utils/audit";
import { actionLabel, formatDateTime, normalizePlate, shortId } from "../utils/format";

const actionOptions: AuditAction[] = ["ENTRY", "OCR_PROCESSING", "EXIT_PAYMENT", "POISON_MESSAGE"];

const actionClasses: Record<string, string> = {
  ENTRY: "bg-blue-100 text-blue-800",
  OCR_PROCESSING: "bg-amber-100 text-amber-800",
  EXIT_PAYMENT: "bg-green-100 text-green-800",
  POISON_MESSAGE: "bg-red-100 text-red-800",
};

function matchesText(event: AuditEvent, text: string): boolean {
  if (event.entity_id.startsWith(text.toLowerCase())) return true;
  const plateQuery = normalizePlate(text);
  const plate = plateOf(event.details);
  return plateQuery !== "" && plate !== undefined && normalizePlate(plate).includes(plateQuery);
}

export default function AuditPage() {
  const [events, setEvents] = useState<AuditEvent[]>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [action, setAction] = useState("");
  const [text, setText] = useState("");

  // Só altera o estado na resposta; o carregamento inicial parte de loading = true.
  const load = useCallback(
    () =>
      listAuditEvents()
        .then(
          (result) => {
            setEvents(sortByNewest(result));
            setError(undefined);
          },
          (err: unknown) => setError(errorMessage(err)),
        )
        .finally(() => setLoading(false)),
    [],
  );

  useEffect(() => {
    void load();
  }, [load]);

  function reload() {
    setLoading(true);
    setError(undefined);
    void load();
  }

  const trimmedText = text.trim();
  const filtered = useMemo(
    () =>
      (events ?? []).filter(
        (e) => (action === "" || e.action === action) && (trimmedText === "" || matchesText(e, trimmedText)),
      ),
    [events, action, trimmedText],
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Auditoria</h1>
        <button
          type="button"
          onClick={reload}
          disabled={loading}
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Atualizar
        </button>
      </div>

      <div className="flex flex-wrap gap-4">
        <div className="space-y-1">
          <label htmlFor="action-filter" className="block font-medium">
            Ação
          </label>
          <select
            id="action-filter"
            value={action}
            onChange={(e) => setAction(e.target.value)}
            className="rounded-md border border-slate-300 bg-white px-3 py-2"
          >
            <option value="">Todas</option>
            {actionOptions.map((a) => (
              <option key={a} value={a}>
                {actionLabel(a)}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <label htmlFor="text-filter" className="block font-medium">
            Sessão ou placa
          </label>
          <input
            id="text-filter"
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            className="rounded-md border border-slate-300 px-3 py-2"
          />
        </div>
      </div>

      {error ? (
        <Alert variant="error" onRetry={reload}>
          {error}
        </Alert>
      ) : events === undefined ? (
        <div className="flex justify-center py-8 text-slate-500">
          <Spinner className="size-8" />
        </div>
      ) : events.length === 0 ? (
        <p className="text-slate-600">Nenhum evento registrado.</p>
      ) : (
        <>
          <p className="text-sm text-slate-600">
            Exibindo {filtered.length} eventos (a API retorna os 50 mais recentes)
          </p>
          <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-100 text-slate-600">
                <tr>
                  <th scope="col" className="px-3 py-2">Data/hora</th>
                  <th scope="col" className="px-3 py-2">Ação</th>
                  <th scope="col" className="px-3 py-2">Sessão</th>
                  <th scope="col" className="px-3 py-2">Placa</th>
                  <th scope="col" className="px-3 py-2">Detalhes</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((e) => (
                  <tr key={e.id} className="border-t border-slate-200">
                    <td className="px-3 py-2 whitespace-nowrap">{formatDateTime(e.timestamp)}</td>
                    <td className="px-3 py-2">
                      <span
                        className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${actionClasses[e.action] ?? "bg-slate-100 text-slate-800"}`}
                      >
                        {actionLabel(e.action)}
                      </span>
                    </td>
                    <td className="px-3 py-2 font-mono" title={e.entity_id}>
                      {shortId(e.entity_id)}
                    </td>
                    <td className="px-3 py-2 font-mono">{plateOf(e.details) ?? "—"}</td>
                    <td className="px-3 py-2">{describeDetails(e.details)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
