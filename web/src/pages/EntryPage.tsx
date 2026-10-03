import { useCallback, useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import Alert from "../components/Alert";
import Spinner from "../components/Spinner";
import TicketCard from "../components/TicketCard";
import { usePolling } from "../hooks/usePolling";
import { createEntry, errorMessage, getSession, isTransientError } from "../services/api";
import type { Session } from "../types/api";

const MAX_PHOTO_BYTES = 10 * 1024 * 1024;
// Acompanhamento do OCR em duas fases: rápida enquanto o carro está na cancela e lenta
// até o prazo em que o worker desiste de uma placa ilegível. O worker só marca FAILED na
// 3ª entrega da mensagem no SQS (visibility timeout de 300 s), ~10 min após a entrada.
const OCR_FAST_INTERVAL_MS = 2000;
const OCR_FAST_PHASE_MS = 60_000;
const OCR_SLOW_INTERVAL_MS = 15_000;
const OCR_TRACKING_LIMIT_MS = 12 * 60_000;

// fast/slow: consultando · expired: prazo esgotado · stopped: erro definitivo na consulta
type TrackingPhase = "fast" | "slow" | "expired" | "stopped";

interface Tracking {
  id: string; // ticket acompanhado: respostas atrasadas de outro ticket são ignoradas
  round: number; // incrementa a cada "Tentar novamente" para reiniciar os prazos
  phase: TrackingPhase;
  offline: boolean; // última consulta falhou com erro transitório
  error?: string; // mensagem do erro definitivo (phase = "stopped")
}

function validatePhoto(file: File | undefined): string | undefined {
  if (!file) return "Selecione a foto do veículo.";
  if (!file.type.startsWith("image/")) return "O arquivo precisa ser uma imagem.";
  if (file.size > MAX_PHOTO_BYTES) return "A imagem deve ter no máximo 10 MB.";
  return undefined;
}

export default function EntryPage() {
  const [file, setFile] = useState<File>();
  const [fileError, setFileError] = useState<string>();
  const [previewUrl, setPreviewUrl] = useState<string>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string>();
  const [ticket, setTicket] = useState<Session>();
  const [tracking, setTracking] = useState<Tracking>();

  // Revoga a URL da pré-visualização ao trocar a imagem ou desmontar.
  useEffect(() => {
    if (!previewUrl) return;
    return () => URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  // Atualiza o acompanhamento só se ainda for do mesmo ticket e da mesma rodada.
  const updateTracking = useCallback(
    (id: string, round: number | undefined, change: Partial<Tracking>) =>
      setTracking((current) =>
        current?.id === id && (round === undefined || current.round === round)
          ? { ...current, ...change }
          : current,
      ),
    [],
  );

  // Erros transitórios (rede, 5xx, 429) mantêm a consulta e só exibem um aviso; erro
  // definitivo encerra o acompanhamento com mensagem. O ticket já é válido em ambos.
  const ticketId = ticket?.id;
  const fetchTicket = useCallback(async () => {
    const id = ticketId ?? "";
    try {
      const session = await getSession(id);
      setTicket((current) => (current?.id === session.id ? session : current));
      updateTracking(id, undefined, { offline: false });
      return session;
    } catch (err) {
      updateTracking(
        id,
        undefined,
        isTransientError(err)
          ? { offline: true }
          : { phase: "stopped", offline: false, error: errorMessage(err, "session") },
      );
      throw err;
    }
  }, [ticketId, updateTracking]);

  const polling =
    ticket?.status === "PROCESSING" &&
    tracking !== undefined &&
    tracking.id === ticketId &&
    (tracking.phase === "fast" || tracking.phase === "slow");
  const interval = tracking?.phase === "slow" ? OCR_SLOW_INTERVAL_MS : OCR_FAST_INTERVAL_MS;
  usePolling(fetchTicket, interval, { enabled: polling });

  const trackingId = tracking?.id;
  const trackingRound = tracking?.round;
  useEffect(() => {
    if (trackingId === undefined) return;
    const slow = setTimeout(
      () => updateTracking(trackingId, trackingRound, { phase: "slow" }),
      OCR_FAST_PHASE_MS,
    );
    const expire = setTimeout(
      () => updateTracking(trackingId, trackingRound, { phase: "expired" }),
      OCR_TRACKING_LIMIT_MS,
    );
    return () => {
      clearTimeout(slow);
      clearTimeout(expire);
    };
  }, [trackingId, trackingRound, updateTracking]);

  function restartTracking() {
    setTracking((current) =>
      current && {
        ...current,
        round: current.round + 1,
        phase: "fast",
        offline: false,
        error: undefined,
      },
    );
  }

  function selectFile(selected: File | undefined) {
    setFile(selected);
    setPreviewUrl(selected ? URL.createObjectURL(selected) : undefined);
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0];
    const validation = selected ? validatePhoto(selected) : undefined;
    setError(undefined);
    setFileError(validation);
    selectFile(validation ? undefined : selected);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const validation = validatePhoto(file);
    if (validation || !file) {
      setFileError(validation);
      return;
    }

    setSubmitting(true);
    setError(undefined);
    try {
      const session = await createEntry(file);
      setTicket(session);
      setTracking({ id: session.id, round: 0, phase: "fast", offline: false });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  function handleReset() {
    selectFile(undefined);
    setFileError(undefined);
    setError(undefined);
    setTicket(undefined);
    setTracking(undefined);
  }

  return (
    <div className="mx-auto max-w-xl space-y-6">
      <h1 className="text-2xl font-bold">Totem de Entrada</h1>

      {ticket ? (
        <div className="space-y-4">
          <TicketCard session={ticket} />
          {ticket.status === "PARKED" && ticket.license_plate && (
            <Alert variant="success">
              Placa identificada: <strong className="font-mono">{ticket.license_plate}</strong>
            </Alert>
          )}
          {ticket.status === "FAILED" && (
            <Alert variant="warning">Não foi possível ler a placa. Procure o operador.</Alert>
          )}
          {ticket.status === "PROCESSING" && <TrackingStatus tracking={tracking} onRetry={restartTracking} />}
          <button
            type="button"
            onClick={handleReset}
            className="w-full rounded-md bg-slate-800 px-4 py-3 font-semibold text-white hover:bg-slate-700"
          >
            Nova entrada
          </button>
        </div>
      ) : (
        <form
          onSubmit={handleSubmit}
          noValidate
          className="space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
        >
          <div className="space-y-2">
            <label htmlFor="photo" className="block font-medium">
              Foto frontal do veículo
            </label>
            <input
              id="photo"
              type="file"
              accept="image/*"
              capture="environment"
              onChange={handleFileChange}
              disabled={submitting}
              aria-invalid={fileError !== undefined}
              aria-describedby={fileError ? "photo-error" : undefined}
              className="block w-full text-sm file:mr-4 file:rounded-md file:border-0 file:bg-slate-100 file:px-4 file:py-2 file:font-medium hover:file:bg-slate-200"
            />
            {fileError && (
              <p id="photo-error" className="text-sm text-red-700">
                {fileError}
              </p>
            )}
          </div>

          {previewUrl && (
            <img
              src={previewUrl}
              alt="Pré-visualização da foto selecionada"
              className="max-h-64 w-full rounded-md object-contain"
            />
          )}

          {error && <Alert variant="error">{error}</Alert>}

          <button
            type="submit"
            disabled={!file || submitting}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-blue-600 px-4 py-4 text-lg font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {submitting ? (
              <>
                <Spinner label="Enviando" />
                Enviando foto...
              </>
            ) : (
              "Emitir ticket"
            )}
          </button>
        </form>
      )}
    </div>
  );
}

function TrackingStatus({ tracking, onRetry }: { tracking?: Tracking; onRetry: () => void }) {
  if (!tracking) return null;
  if (tracking.phase === "stopped") {
    return (
      <Alert variant="error" onRetry={onRetry}>
        Não foi possível acompanhar a leitura da placa: {tracking.error} O ticket é válido.
      </Alert>
    );
  }
  if (tracking.phase === "expired") {
    return (
      <Alert variant="warning" onRetry={onRetry}>
        Não foi possível confirmar a leitura da placa. O ticket é válido; procure o operador se
        precisar.
      </Alert>
    );
  }
  if (tracking.offline) {
    return <Alert variant="warning">Não foi possível consultar o servidor. Tentando novamente...</Alert>;
  }
  if (tracking.phase === "slow") {
    return (
      <Alert variant="info">
        A leitura da placa está demorando. O ticket é válido e a cancela já foi liberada.
      </Alert>
    );
  }
  return null;
}
