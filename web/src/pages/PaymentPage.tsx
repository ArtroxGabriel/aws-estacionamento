import { useMemo, useRef, useState } from "react";
import Alert from "../components/Alert";
import Spinner from "../components/Spinner";
import StatusBadge from "../components/StatusBadge";
import { usePolling } from "../hooks/usePolling";
import {
  deleteSession,
  errorMessage,
  INVALID_PLATE_MESSAGE,
  listSessions,
  payExit,
  updatePlate,
} from "../services/api";
import type { Session } from "../types/api";
import {
  formatCurrency,
  formatDateTime,
  formatDuration,
  isValidPlate,
  normalizePlate,
  shortId,
} from "../utils/format";

// Sessões que podem pagar a saída: PARKED e também FAILED (placa não lida pelo OCR;
// o totem orienta o motorista a procurar o operador).
async function listPayableSessions(): Promise<Session[]> {
  const [parked, failed] = await Promise.all([listSessions("PARKED"), listSessions("FAILED")]);
  return [...parked, ...failed];
}

function matches(session: Session, query: string): boolean {
  const plateQuery = normalizePlate(query);
  if (plateQuery && normalizePlate(session.license_plate ?? "").includes(plateQuery)) return true;
  return session.id.startsWith(query.toLowerCase());
}

const buttonBase = "rounded-md px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed";

export default function PaymentPage() {
  // "Tempo estacionado" é recalculado a cada atualização da lista (lastUpdated).
  const { data, error, loading, lastUpdated, refresh } = usePolling(listPayableSessions, 15_000);
  const [query, setQuery] = useState("");
  const [confirmingId, setConfirmingId] = useState<string>();
  const [payingId, setPayingId] = useState<string>();
  const [paidIds, setPaidIds] = useState<ReadonlySet<string>>(new Set());
  const [receipt, setReceipt] = useState<Session>();
  const [payError, setPayError] = useState<string>();
  // Placa digitada pelo operador (OCR não leu ou leu errado) e exclusão de registro.
  const [editingId, setEditingId] = useState<string>();
  const [plateInput, setPlateInput] = useState("");
  const [savingPlate, setSavingPlate] = useState(false);
  const [deletingConfirmId, setDeletingConfirmId] = useState<string>();
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState<string>();
  // Bloqueia cliques repetidos antes do re-render desabilitar os botões (evita pagamento duplo).
  const payingRef = useRef(false);

  const sessions = useMemo(
    () =>
      (data ?? [])
        .filter((s) => !paidIds.has(s.id))
        .sort((a, b) => Date.parse(a.entered_at) - Date.parse(b.entered_at)),
    [data, paidIds],
  );

  const trimmedQuery = query.trim();
  const filtered = useMemo(
    () => (trimmedQuery ? sessions.filter((s) => matches(s, trimmedQuery)) : sessions),
    [sessions, trimmedQuery],
  );

  function startEditing(session: Session) {
    setEditingId(session.id);
    setPlateInput(session.license_plate ?? "");
    setConfirmingId(undefined);
    setDeletingConfirmId(undefined);
    setPayError(undefined);
  }

  async function handleSavePlate(id: string) {
    if (!isValidPlate(plateInput)) {
      setPayError(INVALID_PLATE_MESSAGE);
      return;
    }
    setSavingPlate(true);
    setPayError(undefined);
    try {
      const updated = await updatePlate(id, normalizePlate(plateInput));
      setNotice(`Placa ${updated.license_plate ?? ""} registrada para o ticket ${shortId(id)}.`);
      setEditingId(undefined);
      refresh();
    } catch (err) {
      setPayError(errorMessage(err, "plate"));
    } finally {
      setSavingPlate(false);
    }
  }

  async function handleDelete(id: string) {
    setDeleting(true);
    setPayError(undefined);
    try {
      await deleteSession(id);
      setNotice(`Registro do ticket ${shortId(id)} excluído.`);
      // Some da lista na hora, sem esperar o próximo polling.
      setPaidIds((prev) => new Set(prev).add(id));
      refresh();
    } catch (err) {
      setPayError(errorMessage(err, "session"));
    } finally {
      setDeleting(false);
      setDeletingConfirmId(undefined);
    }
  }

  async function handleConfirm(id: string) {
    if (payingRef.current) return;
    payingRef.current = true;
    setPayingId(id);
    setPayError(undefined);
    setReceipt(undefined);
    setNotice(undefined);
    try {
      const paid = await payExit(id);
      setReceipt(paid);
      setPaidIds((prev) => new Set(prev).add(id));
      refresh();
    } catch (err) {
      setPayError(errorMessage(err, "session"));
    } finally {
      payingRef.current = false;
      setPayingId(undefined);
      setConfirmingId(undefined);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Caixa / Saída</h1>
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          className={`${buttonBase} border border-slate-300 bg-white hover:bg-slate-100 disabled:opacity-60`}
        >
          Atualizar
        </button>
      </div>

      {receipt && (
        <Alert variant="success">
          <p className="font-semibold">Pagamento confirmado. Cancela de saída liberada.</p>
          <p>
            Placa: <span className="font-mono">{receipt.license_plate ?? "—"}</span> · Valor pago:{" "}
            {receipt.amount_paid !== undefined ? formatCurrency(receipt.amount_paid) : "—"} · Saída:{" "}
            {receipt.exited_at ? formatDateTime(receipt.exited_at) : "—"}
          </p>
        </Alert>
      )}
      {notice && <Alert variant="success">{notice}</Alert>}
      {payError && <Alert variant="error">{payError}</Alert>}

      <div className="space-y-1">
        <label htmlFor="search" className="block font-medium">
          Buscar por placa ou ID
        </label>
        <input
          id="search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="ABC1D23 ou 9f2c4e1a"
          className="w-full max-w-sm rounded-md border border-slate-300 px-3 py-2"
        />
      </div>

      {error && data === undefined ? (
        <Alert variant="error" onRetry={refresh}>
          {errorMessage(error)}
        </Alert>
      ) : data === undefined ? (
        <div className="flex justify-center py-8 text-slate-500">
          <Spinner className="size-8" />
        </div>
      ) : (
        <>
          {error && (
            <Alert variant="warning">Sem conexão — exibindo a última lista carregada.</Alert>
          )}
          {sessions.length === 0 ? (
            <p className="text-slate-600">Nenhum veículo estacionado.</p>
          ) : filtered.length === 0 ? (
            <p className="text-slate-600">Nenhum veículo encontrado para '{trimmedQuery}'.</p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-100 text-slate-600">
                  <tr>
                    <th scope="col" className="px-3 py-2">Placa</th>
                    <th scope="col" className="px-3 py-2">Ticket</th>
                    <th scope="col" className="px-3 py-2">Entrada</th>
                    <th scope="col" className="px-3 py-2">Tempo estacionado</th>
                    <th scope="col" className="px-3 py-2">Ação</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((s) => {
                    const paying = payingId === s.id;
                    return (
                      <tr key={s.id} className="border-t border-slate-200">
                        <td className="px-3 py-2 font-mono font-semibold">
                          {s.license_plate ?? (s.status === "FAILED" ? <StatusBadge status="FAILED" /> : "—")}
                        </td>
                        <td className="px-3 py-2 font-mono" title={s.id}>{shortId(s.id)}</td>
                        <td className="px-3 py-2 whitespace-nowrap">{formatDateTime(s.entered_at)}</td>
                        <td className="px-3 py-2">{formatDuration(s.entered_at, lastUpdated)}</td>
                        <td className="px-3 py-2">
                          {editingId === s.id ? (
                            <form
                              className="flex flex-wrap items-center gap-2"
                              onSubmit={(e) => {
                                e.preventDefault();
                                void handleSavePlate(s.id);
                              }}
                            >
                              <label htmlFor={`plate-${s.id}`} className="sr-only">
                                Placa do veículo
                              </label>
                              <input
                                id={`plate-${s.id}`}
                                value={plateInput}
                                onChange={(e) => setPlateInput(e.target.value)}
                                placeholder="ABC1D23"
                                maxLength={8}
                                autoFocus
                                className="w-28 rounded-md border border-slate-300 px-2 py-1 font-mono uppercase"
                              />
                              <button
                                type="submit"
                                disabled={savingPlate}
                                className={`${buttonBase} inline-flex items-center gap-2 bg-purple-600 text-white hover:bg-purple-700 disabled:bg-purple-400`}
                              >
                                {savingPlate && <Spinner label="Salvando placa" className="size-4" />}
                                Salvar placa
                              </button>
                              <button
                                type="button"
                                onClick={() => setEditingId(undefined)}
                                disabled={savingPlate}
                                className={`${buttonBase} border border-slate-300 hover:bg-slate-100 disabled:opacity-60`}
                              >
                                Cancelar
                              </button>
                            </form>
                          ) : deletingConfirmId === s.id ? (
                            <div className="flex flex-wrap gap-2">
                              <button
                                type="button"
                                onClick={() => handleDelete(s.id)}
                                disabled={deleting}
                                className={`${buttonBase} inline-flex items-center gap-2 bg-red-600 text-white hover:bg-red-700 disabled:bg-red-400`}
                              >
                                {deleting && <Spinner label="Excluindo" className="size-4" />}
                                Confirmar exclusão
                              </button>
                              <button
                                type="button"
                                onClick={() => setDeletingConfirmId(undefined)}
                                disabled={deleting}
                                className={`${buttonBase} border border-slate-300 hover:bg-slate-100 disabled:opacity-60`}
                              >
                                Cancelar
                              </button>
                            </div>
                          ) : confirmingId === s.id ? (
                            <div className="flex flex-wrap gap-2">
                              <button
                                type="button"
                                onClick={() => handleConfirm(s.id)}
                                disabled={paying}
                                className={`${buttonBase} inline-flex items-center gap-2 bg-green-600 text-white hover:bg-green-700 disabled:bg-green-400`}
                              >
                                {paying && <Spinner label="Processando pagamento" className="size-4" />}
                                Confirmar pagamento
                              </button>
                              <button
                                type="button"
                                onClick={() => setConfirmingId(undefined)}
                                disabled={paying}
                                className={`${buttonBase} border border-slate-300 hover:bg-slate-100 disabled:opacity-60`}
                              >
                                Cancelar
                              </button>
                            </div>
                          ) : (
                            <div className="flex flex-wrap gap-2">
                              <button
                                type="button"
                                onClick={() => setConfirmingId(s.id)}
                                disabled={payingId !== undefined}
                                className={`${buttonBase} bg-blue-600 text-white hover:bg-blue-700 disabled:bg-slate-300`}
                              >
                                Pagar e liberar
                              </button>
                              <button
                                type="button"
                                onClick={() => startEditing(s)}
                                disabled={payingId !== undefined}
                                className={`${buttonBase} border border-purple-300 text-purple-700 hover:bg-purple-50 disabled:opacity-60`}
                              >
                                {s.license_plate ? "Corrigir placa" : "Informar placa"}
                              </button>
                              <button
                                type="button"
                                onClick={() => {
                                  setDeletingConfirmId(s.id);
                                  setConfirmingId(undefined);
                                  setEditingId(undefined);
                                }}
                                disabled={payingId !== undefined}
                                className={`${buttonBase} border border-red-300 text-red-700 hover:bg-red-50 disabled:opacity-60`}
                              >
                                Excluir
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
