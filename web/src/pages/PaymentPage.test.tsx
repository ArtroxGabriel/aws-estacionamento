import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, listSessions, payExit } from "../services/api";
import type { Session, SessionStatusFilter } from "../types/api";
import PaymentPage from "./PaymentPage";

vi.mock("../services/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/api")>()),
  listSessions: vi.fn(),
  payExit: vi.fn(),
}));

const mockedListSessions = vi.mocked(listSessions);
const mockedPayExit = vi.mocked(payExit);

const minutesAgo = (min: number) => new Date(Date.now() - min * 60_000 - 30_000).toISOString();

const older: Session = {
  id: "aaaa1111bbbb2222",
  license_plate: "ABC1D23",
  status: "PARKED",
  s3_photo_key: "photos/a.jpg",
  entered_at: minutesAgo(125),
};
const newer: Session = {
  id: "cccc3333dddd4444",
  license_plate: "XYZ9876",
  status: "PARKED",
  s3_photo_key: "photos/c.jpg",
  entered_at: minutesAgo(12),
};
const failed: Session = {
  id: "eeee5555ffff6666",
  status: "FAILED",
  s3_photo_key: "photos/e.jpg",
  entered_at: minutesAgo(30),
};

afterEach(() => {
  vi.resetAllMocks();
});

// A página consulta PARKED e FAILED; o mock responde por status.
function mockSessions(sessions: Session[]) {
  mockedListSessions.mockImplementation(async (status?: SessionStatusFilter) =>
    sessions.filter((s) => s.status === status),
  );
}

async function renderWith(sessions: Session[]) {
  mockSessions(sessions);
  const user = userEvent.setup();
  render(<PaymentPage />);
  if (sessions.length > 0) await screen.findByRole("table");
  return { user };
}

function rows() {
  return screen.getAllByRole("row").slice(1); // sem o cabeçalho
}

describe("PaymentPage", () => {
  it("renderiza as linhas ordenadas pela entrada mais antiga com placa e duração", async () => {
    await renderWith([newer, older]);

    const [first, second] = rows();
    expect(within(first).getByText("ABC1D23")).toBeInTheDocument();
    expect(within(first).getByText("aaaa1111")).toBeInTheDocument();
    expect(within(first).getByText("2h 05min")).toBeInTheDocument();
    expect(within(second).getByText("XYZ9876")).toBeInTheDocument();
    expect(within(second).getByText("12min")).toBeInTheDocument();
  });

  it("lista também as sessões com falha no OCR", async () => {
    await renderWith([older, failed]);

    expect(mockedListSessions).toHaveBeenCalledWith("PARKED");
    expect(mockedListSessions).toHaveBeenCalledWith("FAILED");
    expect(rows()).toHaveLength(2);
    expect(within(rows()[1]).getByText("Falha no OCR")).toBeInTheDocument();
    expect(within(rows()[1]).getByText("eeee5555")).toBeInTheDocument();
  });

  it("busca por placa normalizada e por prefixo de ID", async () => {
    const { user } = await renderWith([older, newer]);
    const search = screen.getByLabelText("Buscar por placa ou ID");

    await user.type(search, "abc-1d23");
    expect(rows()).toHaveLength(1);
    expect(screen.getByText("ABC1D23")).toBeInTheDocument();

    await user.clear(search);
    await user.type(search, "CCCC33");
    expect(rows()).toHaveLength(1);
    expect(screen.getByText("XYZ9876")).toBeInTheDocument();

    await user.clear(search);
    await user.type(search, "zzz");
    expect(screen.getByText("Nenhum veículo encontrado para 'zzz'.")).toBeInTheDocument();
  });

  it("Confirmar chama payExit uma única vez mesmo com dois cliques", async () => {
    mockedPayExit.mockReturnValue(new Promise(() => {}));
    const { user } = await renderWith([older]);

    await user.click(screen.getByRole("button", { name: "Pagar e liberar" }));
    const confirm = screen.getByRole("button", { name: /Confirmar pagamento/ });
    await user.click(confirm);
    await user.click(confirm);

    expect(mockedPayExit).toHaveBeenCalledTimes(1);
    expect(mockedPayExit).toHaveBeenCalledWith(older.id);
    expect(confirm).toBeDisabled();
  });

  it("Cancelar não chama a API e volta ao botão de pagamento", async () => {
    const { user } = await renderWith([older]);

    await user.click(screen.getByRole("button", { name: "Pagar e liberar" }));
    await user.click(screen.getByRole("button", { name: "Cancelar" }));

    expect(mockedPayExit).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Pagar e liberar" })).toBeInTheDocument();
  });

  it("sucesso exibe o recibo com o valor vindo da API e remove a linha", async () => {
    mockedPayExit.mockResolvedValue({
      ...older,
      status: "PAID",
      amount_paid: 10,
      exited_at: "2026-10-03T16:10:00Z",
    });
    const { user } = await renderWith([older, newer]); // a API ainda não refletiu o pagamento

    await user.click(within(rows()[0]).getByRole("button", { name: "Pagar e liberar" }));
    await user.click(screen.getByRole("button", { name: /Confirmar pagamento/ }));

    const receipt = await screen.findByRole("status");
    expect(receipt).toHaveTextContent("Cancela de saída liberada");
    expect(receipt).toHaveTextContent("ABC1D23");
    expect(receipt).toHaveTextContent(/R\$\s10,00/);
    expect(rows()).toHaveLength(1);
    expect(within(screen.getByRole("table")).queryByText("ABC1D23")).not.toBeInTheDocument();
    expect(mockedListSessions).toHaveBeenCalledTimes(4); // carga inicial + refresh (PARKED e FAILED)
  });

  it("erro 404 exibe Sessão não encontrada e a linha volta ao normal", async () => {
    mockedPayExit.mockRejectedValue(new ApiError(404, "session not found"));
    const { user } = await renderWith([older]);

    await user.click(screen.getByRole("button", { name: "Pagar e liberar" }));
    await user.click(screen.getByRole("button", { name: /Confirmar pagamento/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Sessão não encontrada.");
    expect(screen.getByRole("button", { name: "Pagar e liberar" })).toBeEnabled();
  });

  it("erro 409 exibe que a sessão não está pronta para pagamento", async () => {
    mockedPayExit.mockRejectedValue(new ApiError(409, "session is not in a payable status"));
    const { user } = await renderWith([older]);

    await user.click(screen.getByRole("button", { name: "Pagar e liberar" }));
    await user.click(screen.getByRole("button", { name: /Confirmar pagamento/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "A sessão não está pronta para pagamento.",
    );
  });

  it("lista vazia exibe Nenhum veículo estacionado", async () => {
    await renderWith([]);
    expect(await screen.findByText("Nenhum veículo estacionado.")).toBeInTheDocument();
  });

  it("erro ao carregar a lista exibe Alert com Tentar novamente", async () => {
    mockedListSessions.mockRejectedValueOnce(new ApiError(0, "x"));
    const user = userEvent.setup();
    render(<PaymentPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Não foi possível conectar à API.");
    mockSessions([older]);
    await user.click(screen.getByRole("button", { name: "Tentar novamente" }));
    expect(await screen.findByText("ABC1D23")).toBeInTheDocument();
  });
});
