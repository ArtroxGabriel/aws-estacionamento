import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, deleteSession, listSessions, payExit, updatePlate } from "../services/api";
import type { Session, SessionStatusFilter } from "../types/api";
import PaymentPage from "./PaymentPage";

vi.mock("../services/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/api")>()),
  listSessions: vi.fn(),
  payExit: vi.fn(),
  updatePlate: vi.fn(),
  deleteSession: vi.fn(),
}));

const mockedListSessions = vi.mocked(listSessions);
const mockedPayExit = vi.mocked(payExit);
const mockedUpdatePlate = vi.mocked(updatePlate);
const mockedDeleteSession = vi.mocked(deleteSession);

const minutesAgo = (min: number) => new Date(Date.now() - min * 60_000 - 30_000).toISOString();

const older: Session = {
  id: "aaaa1111bbbb2222",
  license_plate: "ABC1D23",
  status: "PARKED",
  plate_status: "READ",
  s3_photo_key: "photos/a.jpg",
  entered_at: minutesAgo(125),
};
const newer: Session = {
  id: "cccc3333dddd4444",
  license_plate: "XYZ9876",
  status: "PARKED",
  plate_status: "MANUAL",
  s3_photo_key: "photos/c.jpg",
  entered_at: minutesAgo(12),
};
// Carros estacionados cuja foto não deu placa: pagam pelo ticket como qualquer outro.
const unread: Session = {
  id: "eeee5555ffff6666",
  status: "PARKED",
  plate_status: "UNREADABLE",
  s3_photo_key: "photos/e.jpg",
  entered_at: minutesAgo(30),
};
const pending: Session = {
  id: "9999aaaa8888bbbb",
  status: "PARKED",
  plate_status: "PENDING",
  s3_photo_key: "photos/p.jpg",
  entered_at: minutesAgo(1),
};

afterEach(() => {
  vi.resetAllMocks();
});

// A página consulta só PARKED; o mock responde por status.
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

  it("consulta uma única lista: todo carro no pátio está PARKED", async () => {
    await renderWith([older]);

    expect(mockedListSessions).toHaveBeenCalledTimes(1);
    expect(mockedListSessions).toHaveBeenCalledWith("PARKED");
  });

  it("lista os carros sem placa lida como qualquer outro, sem marca de falha", async () => {
    await renderWith([older, unread, pending]);

    expect(rows()).toHaveLength(3);
    const [, unreadRow, pendingRow] = rows();
    expect(within(unreadRow).getByText("Sem placa")).toBeInTheDocument();
    expect(within(unreadRow).getByText("eeee5555")).toBeInTheDocument();
    expect(within(unreadRow).getByRole("button", { name: "Pagar" })).toBeEnabled();
    expect(within(pendingRow).getByText("Lendo placa...")).toBeInTheDocument();
    expect(within(pendingRow).getByRole("button", { name: "Pagar" })).toBeEnabled();
    expect(screen.queryByText(/falha/i)).not.toBeInTheDocument();
  });

  it("paga pelo ticket um carro sem placa, sem pedir a placa antes", async () => {
    mockedPayExit.mockResolvedValue({
      ...unread,
      status: "PAID",
      amount_paid: 10,
      paid_at: "2026-10-03T16:10:00Z",
    });
    const { user } = await renderWith([unread]);

    await user.click(screen.getByRole("button", { name: "Pagar" }));
    await user.click(screen.getByRole("button", { name: /Confirmar pagamento/ }));

    expect(mockedPayExit).toHaveBeenCalledWith(unread.id);
    expect(mockedUpdatePlate).not.toHaveBeenCalled();
    const receipt = await screen.findByRole("status");
    expect(receipt).toHaveTextContent("Siga para a cancela de saída");
    expect(receipt).toHaveTextContent("eeee5555");
    expect(receipt).toHaveTextContent("sem placa");
  });

  it("mostra a foto de entrada de cada ticket, com link para o tamanho real", async () => {
    await renderWith([older, unread]);

    const photo = screen.getByAltText("Foto de entrada do ticket eeee5555");
    expect(photo).toHaveAttribute("src", "/api/sessions/eeee5555ffff6666/photo");
    expect(photo.closest("a")).toHaveAttribute("href", "/api/sessions/eeee5555ffff6666/photo");
    expect(screen.getByAltText("Foto de entrada do ticket aaaa1111")).toBeInTheDocument();
  });

  it("foto que não carrega vira um aviso discreto e não atrapalha o pagamento", async () => {
    await renderWith([unread]);

    fireEvent.error(screen.getByAltText("Foto de entrada do ticket eeee5555"));

    expect(screen.getByText("Foto indisponível")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pagar" })).toBeEnabled();
  });

  it("busca pelo ticket encontra o carro mesmo sem placa", async () => {
    const { user } = await renderWith([older, unread]);

    await user.type(screen.getByLabelText("Buscar por ticket ou placa"), "eeee55");

    expect(rows()).toHaveLength(1);
    expect(within(rows()[0]).getByText("Sem placa")).toBeInTheDocument();
  });

  it("busca por placa normalizada e por prefixo de ID", async () => {
    const { user } = await renderWith([older, newer]);
    const search = screen.getByLabelText("Buscar por ticket ou placa");

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

    await user.click(screen.getByRole("button", { name: "Pagar" }));
    const confirm = screen.getByRole("button", { name: /Confirmar pagamento/ });
    await user.click(confirm);
    await user.click(confirm);

    expect(mockedPayExit).toHaveBeenCalledTimes(1);
    expect(mockedPayExit).toHaveBeenCalledWith(older.id);
    expect(confirm).toBeDisabled();
  });

  it("Cancelar não chama a API e volta ao botão de pagamento", async () => {
    const { user } = await renderWith([older]);

    await user.click(screen.getByRole("button", { name: "Pagar" }));
    await user.click(screen.getByRole("button", { name: "Cancelar" }));

    expect(mockedPayExit).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Pagar" })).toBeInTheDocument();
  });

  it("sucesso exibe o recibo com o valor vindo da API e remove a linha", async () => {
    mockedPayExit.mockResolvedValue({
      ...older,
      status: "PAID",
      amount_paid: 10,
      paid_at: "2026-10-03T16:10:00Z",
    });
    const { user } = await renderWith([older, newer]); // a API ainda não refletiu o pagamento

    await user.click(within(rows()[0]).getByRole("button", { name: "Pagar" }));
    await user.click(screen.getByRole("button", { name: /Confirmar pagamento/ }));

    const receipt = await screen.findByRole("status");
    expect(receipt).toHaveTextContent("Siga para a cancela de saída");
    expect(receipt).toHaveTextContent("ABC1D23");
    expect(receipt).toHaveTextContent(/R\$\s10,00/);
    expect(rows()).toHaveLength(1);
    expect(within(screen.getByRole("table")).queryByText("ABC1D23")).not.toBeInTheDocument();
    expect(mockedListSessions).toHaveBeenCalledTimes(2); // carga inicial + refresh
  });

  it("erro 404 exibe Sessão não encontrada e a linha volta ao normal", async () => {
    mockedPayExit.mockRejectedValue(new ApiError(404, "session not found"));
    const { user } = await renderWith([older]);

    await user.click(screen.getByRole("button", { name: "Pagar" }));
    await user.click(screen.getByRole("button", { name: /Confirmar pagamento/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Sessão não encontrada.");
    expect(screen.getByRole("button", { name: "Pagar" })).toBeEnabled();
  });

  it("erro 409 exibe que o ticket já foi pago", async () => {
    mockedPayExit.mockRejectedValue(new ApiError(409, "session is not in a payable status"));
    const { user } = await renderWith([older]);

    await user.click(screen.getByRole("button", { name: "Pagar" }));
    await user.click(screen.getByRole("button", { name: /Confirmar pagamento/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Este ticket já foi pago.");
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

describe("PaymentPage — placa manual e exclusão", () => {
  it("Informar placa (opcional) num carro sem placa envia a placa normalizada", async () => {
    const { user } = await renderWith([unread]);
    mockedUpdatePlate.mockResolvedValue({ ...unread, plate_status: "MANUAL", license_plate: "LSN4I49" });

    await user.click(screen.getByRole("button", { name: "Informar placa" }));
    await user.type(screen.getByLabelText("Placa do veículo"), "lsn-4i49");
    await user.click(screen.getByRole("button", { name: "Salvar placa" }));

    expect(mockedUpdatePlate).toHaveBeenCalledWith(unread.id, "LSN4I49");
    expect(await screen.findByText(/Placa LSN4I49 registrada/)).toBeInTheDocument();
  });

  it("placa inválida mostra o formato esperado sem chamar a API", async () => {
    const { user } = await renderWith([unread]);

    await user.click(screen.getByRole("button", { name: "Informar placa" }));
    await user.type(screen.getByLabelText("Placa do veículo"), "12AB");
    await user.click(screen.getByRole("button", { name: "Salvar placa" }));

    expect(await screen.findByText(/Placa inválida/)).toBeInTheDocument();
    expect(mockedUpdatePlate).not.toHaveBeenCalled();
  });

  it("sessão com placa oferece Corrigir placa já preenchido", async () => {
    const { user } = await renderWith([older]);

    await user.click(screen.getByRole("button", { name: "Corrigir placa" }));

    expect(screen.getByLabelText("Placa do veículo")).toHaveValue("ABC1D23");
  });

  it("Excluir pede confirmação, chama deleteSession e remove a linha", async () => {
    const { user } = await renderWith([older, newer]);
    mockedDeleteSession.mockResolvedValue(older);

    const [firstRow] = rows();
    await user.click(within(firstRow).getByRole("button", { name: "Excluir" }));
    expect(mockedDeleteSession).not.toHaveBeenCalled();
    await user.click(within(firstRow).getByRole("button", { name: "Confirmar exclusão" }));

    expect(mockedDeleteSession).toHaveBeenCalledWith(older.id);
    expect(await screen.findByText(/excluído/)).toBeInTheDocument();
    expect(screen.queryByText("ABC1D23")).not.toBeInTheDocument();
  });
});
