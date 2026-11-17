import { useRef, useState, type FormEvent } from "react";
import Spinner from "../components/Spinner";
import {
  ApiError,
  errorMessage,
  exitByPlate,
  exitByTicket,
  INVALID_PLATE_MESSAGE,
  listSessions,
} from "../services/api";
import type { Session } from "../types/api";
import { formatDateTime, isValidPlate, normalizePlate, shortId } from "../utils/format";

// Totem da cancela de saída. A câmera é simulada pelo campo da placa: a API só procura a
// placa entre os carros no pátio cuja placa foi lida ou digitada, sem OCR na hora. Quando a
// placa não resolve, o motorista digita o ticket, que sempre funciona para um carro pago.

type Mode = "plate" | "ticket";

type Outcome =
  | { kind: "open"; session: Session }
  | { kind: "payment_pending" }
  | { kind: "already_exited" }
  | { kind: "not_found"; mode: Mode }
  | { kind: "error"; message: string };

const TICKET_LENGTH = 32;

// O ticket impresso destaca os 8 primeiros caracteres. Um prefixo é resolvido para o ID
// completo entre as sessões conhecidas; só é aceito se apontar para uma sessão.
async function resolveTicket(input: string): Promise<string | undefined> {
  const value = input.trim().toLowerCase();
  if (value.length >= TICKET_LENGTH) return value;
  const matches = (await listSessions("ALL")).filter((s) => s.id.startsWith(value));
  return matches.length === 1 ? matches[0].id : undefined;
}

function outcomeFromError(err: unknown, mode: Mode): Outcome {
  if (err instanceof ApiError) {
    if (err.code === "payment_pending") return { kind: "payment_pending" };
    if (err.code === "already_exited") return { kind: "already_exited" };
    if (err.status === 404) return { kind: "not_found", mode };
    if (err.code === "invalid_plate") return { kind: "error", message: INVALID_PLATE_MESSAGE };
  }
  return { kind: "error", message: errorMessage(err) };
}

const buttonBase =
  "inline-flex items-center justify-center gap-2 rounded-md px-4 py-2 font-medium disabled:cursor-not-allowed";

export default function ExitBarrierPage() {
  const [mode, setMode] = useState<Mode>("plate");
  const [plate, setPlate] = useState("");
  const [ticket, setTicket] = useState("");
  const [sending, setSending] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>();
  const sendingRef = useRef(false);

  function switchMode(next: Mode) {
    setMode(next);
    setOutcome(undefined);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (sendingRef.current) return;

    if (mode === "plate" && !isValidPlate(plate)) {
      setOutcome({ kind: "error", message: INVALID_PLATE_MESSAGE });
      return;
    }
    if (mode === "ticket" && ticket.trim() === "") {
      setOutcome({ kind: "error", message: "Digite o número do ticket." });
      return;
    }

    sendingRef.current = true;
    setSending(true);
    setOutcome(undefined);
    try {
      let session: Session;
      if (mode === "plate") {
        session = await exitByPlate(normalizePlate(plate));
      } else {
        const id = await resolveTicket(ticket);
        if (!id) {
          setOutcome({ kind: "not_found", mode: "ticket" });
          return;
        }
        session = await exitByTicket(id);
      }
      setOutcome({ kind: "open", session });
      setPlate("");
      setTicket("");
    } catch (err) {
      setOutcome(outcomeFromError(err, mode));
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  }

  const open = outcome?.kind === "open";

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Cancela de Saída</h1>

      <BarrierLight open={open} />

      {outcome && <OutcomeMessage outcome={outcome} onUseTicket={() => switchMode("ticket")} />}

      <form onSubmit={submit} className="max-w-md space-y-4 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        {mode === "plate" ? (
          <div className="space-y-1">
            <label htmlFor="exit-plate" className="block font-medium">
              Placa lida pela câmera
            </label>
            <input
              id="exit-plate"
              value={plate}
              onChange={(e) => setPlate(e.target.value)}
              placeholder="ABC1D23"
              maxLength={9}
              autoComplete="off"
              className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-lg uppercase"
            />
            <p className="text-sm text-slate-600">
              Simula a câmera da saída. Abre para um carro pago cuja placa foi lida na entrada ou
              informada no caixa.
            </p>
          </div>
        ) : (
          <div className="space-y-1">
            <label htmlFor="exit-ticket" className="block font-medium">
              Número do ticket
            </label>
            <input
              id="exit-ticket"
              value={ticket}
              onChange={(e) => setTicket(e.target.value)}
              placeholder="9f2c4e1a"
              autoComplete="off"
              className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-lg"
            />
            <p className="text-sm text-slate-600">
              Os 8 caracteres em destaque no ticket bastam. Funciona com ou sem placa lida.
            </p>
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <button
            type="submit"
            disabled={sending}
            className={`${buttonBase} bg-blue-600 text-white hover:bg-blue-700 disabled:bg-blue-400`}
          >
            {sending && <Spinner label="Consultando" className="size-4" />}
            {mode === "plate" ? "Ler placa" : "Validar ticket"}
          </button>
          <button
            type="button"
            onClick={() => switchMode(mode === "plate" ? "ticket" : "plate")}
            disabled={sending}
            className={`${buttonBase} border border-slate-300 hover:bg-slate-100 disabled:opacity-60`}
          >
            {mode === "plate" ? "Digitar o ticket de papel" : "Voltar para a placa"}
          </button>
        </div>
      </form>
    </div>
  );
}

function BarrierLight({ open }: { open: boolean }) {
  return (
    <div
      aria-hidden="true"
      className={`flex h-16 max-w-md items-center justify-center rounded-lg text-lg font-bold tracking-wide text-white ${
        open ? "bg-green-600" : "bg-red-600"
      }`}
    >
      {open ? "CANCELA ABERTA" : "CANCELA FECHADA"}
    </div>
  );
}

function OutcomeMessage({ outcome, onUseTicket }: { outcome: Outcome; onUseTicket: () => void }) {
  const box = "max-w-md rounded-md border px-4 py-3 text-sm";
  switch (outcome.kind) {
    case "open": {
      const s = outcome.session;
      return (
        <div role="status" className={`${box} border-green-300 bg-green-50 text-green-800`}>
          <p className="font-semibold">Cancela aberta. Boa viagem!</p>
          <p>
            Ticket <span className="font-mono">{shortId(s.id)}</span>
            {s.license_plate && (
              <>
                {" "}· Placa <span className="font-mono">{s.license_plate}</span>
              </>
            )}
            {s.exited_at && <> · Saída {formatDateTime(s.exited_at)}</>}
          </p>
        </div>
      );
    }
    case "payment_pending":
      return (
        <div role="alert" className={`${box} border-red-300 bg-red-50 text-red-800`}>
          <p className="font-semibold">Pagamento não localizado.</p>
          <p>Pague no caixa antes de sair.</p>
        </div>
      );
    case "already_exited":
      return (
        <div role="alert" className={`${box} border-amber-300 bg-amber-50 text-amber-800`}>
          <p className="font-semibold">Este veículo já saiu.</p>
          <p>A cancela já foi aberta para este ticket.</p>
        </div>
      );
    case "not_found":
      return (
        <div role="alert" className={`${box} border-amber-300 bg-amber-50 text-amber-800`}>
          {outcome.mode === "plate" ? (
            <>
              <p className="font-semibold">Placa não reconhecida.</p>
              <p>
                Use o ticket de papel.{" "}
                <button type="button" onClick={onUseTicket} className="font-medium underline">
                  Digitar o ticket
                </button>
              </p>
            </>
          ) : (
            <>
              <p className="font-semibold">Ticket não encontrado.</p>
              <p>Confira o número impresso no ticket.</p>
            </>
          )}
        </div>
      );
    case "error":
      return (
        <div role="alert" className={`${box} border-red-300 bg-red-50 text-red-800`}>
          {outcome.message}
        </div>
      );
  }
}
