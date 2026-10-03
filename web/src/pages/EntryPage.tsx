import { useCallback, useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import Alert from "../components/Alert";
import Spinner from "../components/Spinner";
import TicketCard from "../components/TicketCard";
import { usePolling } from "../hooks/usePolling";
import { createEntry, errorMessage, getSession } from "../services/api";
import type { Session } from "../types/api";

const MAX_PHOTO_BYTES = 10 * 1024 * 1024;
const OCR_POLL_INTERVAL_MS = 2000;
const OCR_POLL_TIMEOUT_MS = 60_000;

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
  // IDs do ticket cujo acompanhamento terminou; respostas atrasadas de um ticket antigo
  // não afetam o ticket atual.
  const [expiredId, setExpiredId] = useState<string>();
  const [pollFailedId, setPollFailedId] = useState<string>();

  // Revoga a URL da pré-visualização ao trocar a imagem ou desmontar.
  useEffect(() => {
    if (!previewUrl) return;
    return () => URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  // Acompanhamento do OCR: consulta a sessão enquanto estiver PROCESSING, por no máximo 60 s.
  // Em erro na consulta apenas encerra o acompanhamento: o ticket já é válido.
  const ticketId = ticket?.id;
  const fetchTicket = useCallback(async () => {
    try {
      const session = await getSession(ticketId ?? "");
      setTicket((current) => (current?.id === session.id ? session : current));
      return session;
    } catch (err) {
      setPollFailedId(ticketId);
      throw err;
    }
  }, [ticketId]);
  const enabled =
    ticket?.status === "PROCESSING" && expiredId !== ticketId && pollFailedId !== ticketId;
  usePolling(fetchTicket, OCR_POLL_INTERVAL_MS, { enabled });

  useEffect(() => {
    if (!ticketId) return;
    const timer = setTimeout(() => setExpiredId(ticketId), OCR_POLL_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [ticketId]);

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
      setTicket(await createEntry(file));
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
