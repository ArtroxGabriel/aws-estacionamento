import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, exitByPlate, exitByTicket, listSessions } from "../services/api";
import type { Session } from "../types/api";
import ExitBarrierPage from "./ExitBarrierPage";

vi.mock("../services/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/api")>()),
  exitByPlate: vi.fn(),
  exitByTicket: vi.fn(),
  listSessions: vi.fn(),
}));

const mockedExitByPlate = vi.mocked(exitByPlate);
const mockedExitByTicket = vi.mocked(exitByTicket);
const mockedListSessions = vi.mocked(listSessions);

const ticketId = "9f2c4e1a00000000000000000000beef";
const exited: Session = {
  id: ticketId,
  license_plate: "ABC1D23",
  status: "EXITED",
  plate_status: "READ",
  s3_photo_key: "photos/x.jpg",
  entered_at: "2026-11-17T10:00:00Z",
  paid_at: "2026-11-17T11:00:00Z",
  exited_at: "2026-11-17T11:05:00Z",
};

afterEach(() => {
  vi.resetAllMocks();
});

function setup() {
  const user = userEvent.setup();
  render(<ExitBarrierPage />);
  return { user };
}

async function readPlate(user: ReturnType<typeof userEvent.setup>, plate: string) {
  await user.type(screen.getByLabelText("Placa lida pela câmera"), plate);
  await user.click(screen.getByRole("button", { name: "Ler placa" }));
}

describe("ExitBarrierPage", () => {
  it("começa com a cancela fechada e o campo da placa", () => {
    setup();
    expect(screen.getByRole("heading", { level: 1, name: "Cancela de Saída" })).toBeInTheDocument();
    expect(screen.getByText("CANCELA FECHADA")).toBeInTheDocument();
    expect(screen.getByLabelText("Placa lida pela câmera")).toBeInTheDocument();
  });

  it("abre a cancela para a placa de um carro pago", async () => {
    mockedExitByPlate.mockResolvedValue(exited);
    const { user } = setup();

    await readPlate(user, "abc-1d23");

    expect(mockedExitByPlate).toHaveBeenCalledWith("ABC1D23");
    expect(await screen.findByText("Cancela aberta. Boa viagem!")).toBeInTheDocument();
    expect(screen.getByText("CANCELA ABERTA")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("9f2c4e1a");
  });

  it("manda pagar no caixa quando o carro não pagou", async () => {
    mockedExitByPlate.mockRejectedValue(new ApiError(409, "payment pending", "payment_pending"));
    const { user } = setup();

    await readPlate(user, "ABC1D23");

    expect(await screen.findByText("Pagamento não localizado.")).toBeInTheDocument();
    expect(screen.getByText("Pague no caixa antes de sair.")).toBeInTheDocument();
    expect(screen.getByText("CANCELA FECHADA")).toBeInTheDocument();
  });

  it("avisa que o veículo já saiu", async () => {
    mockedExitByTicket.mockRejectedValue(new ApiError(409, "already exited", "already_exited"));
    const { user } = setup();

    await user.click(screen.getByRole("button", { name: "Digitar o ticket de papel" }));
    await user.type(screen.getByLabelText("Número do ticket"), ticketId);
    await user.click(screen.getByRole("button", { name: "Validar ticket" }));

    expect(await screen.findByText("Este veículo já saiu.")).toBeInTheDocument();
    expect(mockedListSessions).not.toHaveBeenCalled(); // ID completo não precisa de busca
  });

  it("placa não reconhecida leva ao ticket, que abre a cancela", async () => {
    mockedExitByPlate.mockRejectedValue(new ApiError(404, "no car", "plate_not_found"));
    mockedListSessions.mockResolvedValue([{ ...exited, status: "PAID", exited_at: undefined }]);
    mockedExitByTicket.mockResolvedValue({ ...exited, license_plate: undefined, plate_status: "UNREADABLE" });
    const { user } = setup();

    await readPlate(user, "LSN4I49");
    expect(await screen.findByText("Placa não reconhecida.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Digitar o ticket" }));
    // O motorista digita só os 8 caracteres em destaque no ticket.
    await user.type(screen.getByLabelText("Número do ticket"), "9F2C4E1A");
    await user.click(screen.getByRole("button", { name: "Validar ticket" }));

    expect(mockedExitByTicket).toHaveBeenCalledWith(ticketId);
    expect(await screen.findByText("Cancela aberta. Boa viagem!")).toBeInTheDocument();
  });

  it("ticket desconhecido não chama a cancela", async () => {
    mockedListSessions.mockResolvedValue([exited]);
    const { user } = setup();

    await user.click(screen.getByRole("button", { name: "Digitar o ticket de papel" }));
    await user.type(screen.getByLabelText("Número do ticket"), "00000000");
    await user.click(screen.getByRole("button", { name: "Validar ticket" }));

    expect(await screen.findByText("Ticket não encontrado.")).toBeInTheDocument();
    expect(mockedExitByTicket).not.toHaveBeenCalled();
  });

  it("valida a placa no navegador antes de chamar a API", async () => {
    const { user } = setup();

    await readPlate(user, "12");

    expect(await screen.findByRole("alert")).toHaveTextContent("Placa inválida");
    expect(mockedExitByPlate).not.toHaveBeenCalled();
  });

  it("mostra erro de rede sem abrir a cancela", async () => {
    mockedExitByPlate.mockRejectedValue(new ApiError(0, "Não foi possível conectar à API."));
    const { user } = setup();

    await readPlate(user, "ABC1D23");

    expect(await screen.findByRole("alert")).toHaveTextContent("Não foi possível conectar à API.");
    expect(screen.getByText("CANCELA FECHADA")).toBeInTheDocument();
  });
});
