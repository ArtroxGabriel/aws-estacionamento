import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, listAuditEvents } from "../services/api";
import type { AuditEvent } from "../types/api";
import AuditPage from "./AuditPage";

vi.mock("../services/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/api")>()),
  listAuditEvents: vi.fn(),
}));

const mockedListAuditEvents = vi.mocked(listAuditEvents);

const sessionId = "9f2c4e1a7b3d4c5e";
const events: AuditEvent[] = [
  {
    id: `${sessionId}#1`,
    action: "ENTRY",
    entity_id: sessionId,
    timestamp: "2026-10-03T14:05:09.1Z",
    details: { status: "PROCESSING", s3_photo_key: "photos/x.jpg" },
  },
  {
    id: `${sessionId}#3`,
    action: "EXIT_PAYMENT",
    entity_id: sessionId,
    timestamp: "2026-10-03T16:00:00Z",
    details: { status: "PAID", amount_paid: 10 },
  },
  {
    id: `${sessionId}#2`,
    action: "OCR_PROCESSING",
    entity_id: sessionId,
    timestamp: "2026-10-03T14:05:12.123456789Z",
    details: { license_plate: "ABC1D23", session_id: sessionId },
  },
  {
    id: "other#4",
    action: "NEW_ACTION",
    entity_id: "11112222aaaa",
    timestamp: "2026-10-03T13:00:00Z",
    details: { anything: true },
  },
];

afterEach(() => {
  vi.resetAllMocks();
});

async function renderPage(data: AuditEvent[] = events) {
  mockedListAuditEvents.mockResolvedValue(data);
  const user = userEvent.setup();
  render(<AuditPage />);
  if (data.length > 0) await screen.findByRole("table");
  return { user };
}

function rows() {
  return screen.getAllByRole("row").slice(1);
}

describe("AuditPage", () => {
  it("lista os eventos do mais recente para o mais antigo", async () => {
    await renderPage();
    expect(mockedListAuditEvents).toHaveBeenCalledTimes(1);
    expect(rows().map((r) => within(r).getAllByRole("cell")[1].textContent)).toEqual([
      "Pagamento/Saída",
      "Leitura de placa",
      "Entrada",
      "NEW_ACTION",
    ]);
    expect(screen.getByText(/^Exibindo 4 eventos/)).toBeInTheDocument();
  });

  it("filtra por ação Pagamento/Saída", async () => {
    const { user } = await renderPage();
    await user.selectOptions(screen.getByLabelText("Ação"), "Pagamento/Saída");
    expect(rows()).toHaveLength(1);
    expect(within(rows()[0]).getByText("Pagamento/Saída")).toBeInTheDocument();
  });

  it("filtra por placa e por prefixo da sessão", async () => {
    const { user } = await renderPage();
    const input = screen.getByLabelText("Sessão ou placa");

    await user.type(input, "abc-1d");
    expect(rows()).toHaveLength(1);

    await user.clear(input);
    await user.type(input, "1111");
    expect(rows()).toHaveLength(1);
    expect(within(rows()[0]).getByText("NEW_ACTION")).toBeInTheDocument();
  });

  it("exibe a placa para OCR_PROCESSING e — para ENTRY", async () => {
    await renderPage();
    const [exit, ocr, entry] = rows();
    expect(within(ocr).getAllByRole("cell")[3]).toHaveTextContent("ABC1D23");
    expect(within(entry).getAllByRole("cell")[3]).toHaveTextContent("—");
    expect(within(exit).getAllByRole("cell")[4]).toHaveTextContent(/^Pago · R\$\s10,00$/);
    expect(within(entry).getAllByRole("cell")[2]).toHaveAttribute("title", sessionId);
  });

  it("exibe ação desconhecida sem quebrar", async () => {
    await renderPage();
    expect(screen.getByText("NEW_ACTION")).toBeInTheDocument();
  });

  it("lista vazia exibe Nenhum evento registrado", async () => {
    await renderPage([]);
    expect(await screen.findByText("Nenhum evento registrado.")).toBeInTheDocument();
  });

  it("erro exibe Alert e Tentar novamente recarrega", async () => {
    mockedListAuditEvents
      .mockRejectedValueOnce(new ApiError(500, "boom"))
      .mockResolvedValueOnce(events);
    const user = userEvent.setup();
    render(<AuditPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Erro no servidor. Tente novamente.");
    await user.click(screen.getByRole("button", { name: "Tentar novamente" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("Atualizar busca os eventos novamente", async () => {
    const { user } = await renderPage();
    await user.click(screen.getByRole("button", { name: "Atualizar" }));
    expect(mockedListAuditEvents).toHaveBeenCalledTimes(2);
  });
});
