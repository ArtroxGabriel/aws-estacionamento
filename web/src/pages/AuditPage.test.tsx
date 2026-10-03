import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, listAuditEvents, listSessions } from "../services/api";
import type { AuditEvent, Session } from "../types/api";
import AuditPage from "./AuditPage";

vi.mock("../services/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/api")>()),
  listAuditEvents: vi.fn(),
  listSessions: vi.fn(),
}));

const mockedListAuditEvents = vi.mocked(listAuditEvents);
const mockedListSessions = vi.mocked(listSessions);

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

async function renderPage(data: AuditEvent[] = events, sessions: Session[] = []) {
  mockedListAuditEvents.mockResolvedValue(data);
  mockedListSessions.mockResolvedValue(sessions);
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
    expect(rows()).toHaveLength(3); // entrada, leitura e pagamento da mesma sessão

    await user.clear(input);
    await user.type(input, "1111");
    expect(rows()).toHaveLength(1);
    expect(within(rows()[0]).getByText("NEW_ACTION")).toBeInTheDocument();
  });

  it("exibe a placa da sessão em todos os eventos dela, inclusive no pagamento", async () => {
    await renderPage();
    const [exit, ocr, entry, other] = rows();
    const plateCell = (row: HTMLElement) => within(row).getAllByRole("cell")[3];
    expect(plateCell(exit)).toHaveTextContent("ABC1D23");
    expect(plateCell(ocr)).toHaveTextContent("ABC1D23");
    expect(plateCell(entry)).toHaveTextContent("ABC1D23");
    expect(plateCell(other)).toHaveTextContent("—");
    expect(within(exit).getAllByRole("cell")[4]).toHaveTextContent(/^Pago · R\$\s10,00$/);
    expect(within(entry).getAllByRole("cell")[2]).toHaveAttribute("title", sessionId);
  });

  it("entrada exibe o resultado do OCR da sessão em Detalhes", async () => {
    await renderPage();
    const entry = rows()[2];
    expect(within(entry).getAllByRole("cell")[4]).toHaveTextContent("Processado");
  });

  it("entrada ainda sem leitura de placa exibe Processando", async () => {
    await renderPage([events[0]], [
      { id: sessionId, status: "PROCESSING", s3_photo_key: "x", entered_at: "" },
    ]);
    expect(within(rows()[0]).getAllByRole("cell")[4]).toHaveTextContent("Processando");
  });

  it("usa a placa das sessões quando o evento de OCR não está na lista", async () => {
    const payment = events[1];
    await renderPage(
      [payment],
      [{ id: sessionId, license_plate: "BRA2E19", status: "PAID", s3_photo_key: "x", entered_at: "" }],
    );
    expect(mockedListSessions).toHaveBeenCalledWith("ALL");
    expect(within(rows()[0]).getAllByRole("cell")[3]).toHaveTextContent("BRA2E19");
  });

  it("falha ao buscar as sessões não impede a exibição da auditoria", async () => {
    mockedListAuditEvents.mockResolvedValue([events[1]]);
    mockedListSessions.mockRejectedValue(new ApiError(500, "boom"));
    render(<AuditPage />);
    await screen.findByRole("table");
    expect(within(rows()[0]).getAllByRole("cell")[3]).toHaveTextContent("—");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
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
    mockedListSessions.mockResolvedValue([]);
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
