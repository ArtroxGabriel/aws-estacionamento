import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, createEntry, getSession } from "../services/api";
import type { Session } from "../types/api";
import EntryPage from "./EntryPage";

vi.mock("../services/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/api")>()),
  createEntry: vi.fn(),
  getSession: vi.fn(),
}));

const mockedCreateEntry = vi.mocked(createEntry);
const mockedGetSession = vi.mocked(getSession);

// Como a API responde ao POST /entries: o carro já está estacionado e o ticket já vale;
// só a placa ainda vai ser lida.
const session: Session = {
  id: "9f2c4e1a7b3d4c5e6f708192a3b4c5d6",
  status: "PARKED",
  plate_status: "PENDING",
  s3_photo_key: "photos/9f2c_car.jpg",
  entered_at: "2026-10-03T14:05:09Z",
};

// O jsdom não implementa createObjectURL/revokeObjectURL.
beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.resetAllMocks();
});

function setup() {
  // applyAccept: false para conseguir simular a seleção de arquivos que não são imagem.
  const user = userEvent.setup({ applyAccept: false });
  render(<EntryPage />);
  const input = screen.getByLabelText("Foto frontal do veículo");
  const submit = screen.getByRole("button", { name: "Emitir ticket" });
  return { user, input, submit };
}

function photo(name = "car.jpg", type = "image/jpeg") {
  return new File(["img"], name, { type });
}

async function submitPhoto() {
  const ctx = setup();
  await ctx.user.upload(ctx.input, photo());
  await ctx.user.click(ctx.submit);
  return ctx;
}

describe("EntryPage", () => {
  it("desabilita o botão sem arquivo", () => {
    const { submit } = setup();
    expect(submit).toBeDisabled();
  });

  it("exibe pré-visualização da foto selecionada", async () => {
    const { user, input, submit } = setup();
    await user.upload(input, photo());
    expect(screen.getByAltText("Pré-visualização da foto selecionada")).toHaveAttribute(
      "src",
      "blob:preview",
    );
    expect(submit).toBeEnabled();
  });

  it("rejeita arquivo que não é imagem sem chamar a API", async () => {
    const { user, input, submit } = setup();
    await user.upload(input, photo("doc.pdf", "application/pdf"));
    expect(screen.getByText("O arquivo precisa ser uma imagem.")).toBeInTheDocument();
    expect(submit).toBeDisabled();
    expect(mockedCreateEntry).not.toHaveBeenCalled();
  });

  it("rejeita imagem maior que 10 MB sem chamar a API", async () => {
    const { user, input, submit } = setup();
    const big = photo();
    Object.defineProperty(big, "size", { value: 10 * 1024 * 1024 + 1 });
    await user.upload(input, big);
    expect(screen.getByText("A imagem deve ter no máximo 10 MB.")).toBeInTheDocument();
    expect(submit).toBeDisabled();
    expect(mockedCreateEntry).not.toHaveBeenCalled();
  });

  it("envio bem-sucedido exibe o ticket já válido, antes de qualquer leitura de placa", async () => {
    mockedCreateEntry.mockResolvedValue(session);
    mockedGetSession.mockReturnValue(new Promise(() => {}));
    await submitPhoto();

    expect(await screen.findByText("9f2c4e1a")).toBeInTheDocument();
    expect(screen.getByText(session.id)).toBeInTheDocument();
    expect(screen.getByText("Ticket de estacionamento")).toBeInTheDocument();
    expect(screen.getByText("Estacionado")).toBeInTheDocument();
    expect(
      screen.getByText("Cancela liberada. Guarde este ticket: é com ele que você paga a saída."),
    ).toBeInTheDocument();
    expect(screen.getByText("A placa está sendo identificada pela foto...")).toBeInTheDocument();
    expect(mockedGetSession).toHaveBeenCalledWith(session.id);
  });

  it("exibe a placa quando a leitura conclui", async () => {
    mockedCreateEntry.mockResolvedValue(session);
    mockedGetSession.mockResolvedValue({ ...session, plate_status: "READ", license_plate: "ABC1D23" });
    await submitPhoto();

    expect(await screen.findByText("ABC1D23")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Placa identificada: ABC1D23");
    expect(screen.getByText("Estacionado")).toBeInTheDocument();
    expect(screen.queryByText("A placa está sendo identificada pela foto...")).not.toBeInTheDocument();
  });

  it("foto ilegível não manda procurar o operador: o ticket é válido", async () => {
    mockedCreateEntry.mockResolvedValue(session);
    mockedGetSession.mockResolvedValue({ ...session, plate_status: "UNREADABLE" });
    await submitPhoto();

    expect(await screen.findByText(/Não foi possível ler a placa pela foto/)).toBeInTheDocument();
    expect(screen.getByText(/o ticket é válido\s+para a saída/)).toBeInTheDocument();
    expect(screen.queryByText(/operador/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("Estacionado")).toBeInTheDocument();
  });

  it("entrada com a fila fora do ar já chega sem placa e não consulta a leitura", async () => {
    mockedCreateEntry.mockResolvedValue({ ...session, plate_status: "UNREADABLE" });
    await submitPhoto();

    expect(await screen.findByText("9f2c4e1a")).toBeInTheDocument();
    expect(screen.getByText(/Não foi possível ler a placa pela foto/)).toBeInTheDocument();
    expect(mockedGetSession).not.toHaveBeenCalled();
  });

  it("pátio lotado (409) avisa o motorista e não emite ticket", async () => {
    mockedCreateEntry.mockRejectedValue(new ApiError(409, "parking lot is full"));
    await submitPhoto();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Estacionamento lotado. Não há vagas disponíveis no momento.",
    );
    expect(screen.queryByText("Ticket de estacionamento")).not.toBeInTheDocument();
    expect(mockedGetSession).not.toHaveBeenCalled();
  });

  it("erro transitório na consulta mantém o ticket válido e avisa", async () => {
    mockedCreateEntry.mockResolvedValue(session);
    mockedGetSession.mockRejectedValue(new ApiError(500, "boom"));
    await submitPhoto();

    expect(await screen.findByText("9f2c4e1a")).toBeInTheDocument();
    expect(screen.getByText("Estacionado")).toBeInTheDocument();
    expect(
      await screen.findByText("Não foi possível consultar o servidor. Tentando novamente..."),
    ).toBeInTheDocument();
  });

  it("erro no envio exibe Alert e mantém a foto selecionada", async () => {
    mockedCreateEntry.mockRejectedValue(new ApiError(400, "photo is required"));
    const { submit } = await submitPhoto();

    expect(await screen.findByRole("alert")).toHaveTextContent("photo is required");
    expect(screen.getByAltText("Pré-visualização da foto selecionada")).toBeInTheDocument();
    expect(submit).toBeEnabled();
  });

  it("Nova entrada volta ao formulário vazio", async () => {
    mockedCreateEntry.mockResolvedValue(session);
    mockedGetSession.mockReturnValue(new Promise(() => {}));
    const { user } = await submitPhoto();

    await user.click(await screen.findByRole("button", { name: "Nova entrada" }));
    expect(screen.getByRole("button", { name: "Emitir ticket" })).toBeDisabled();
    expect(screen.queryByAltText("Pré-visualização da foto selecionada")).not.toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview");
  });
});

// Cenários dependentes de tempo: relógio controlado, avançado em passos de 1 s para o
// React re-renderizar entre os timers (como no navegador).
describe("EntryPage — acompanhamento da leitura da placa", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  async function advance(ms: number) {
    for (let elapsed = 0; elapsed < ms; elapsed += 1000) {
      await act(() => vi.advanceTimersByTimeAsync(1000));
    }
  }

  async function emitTicket() {
    const user = userEvent.setup({ applyAccept: false, advanceTimers: vi.advanceTimersByTime });
    render(<EntryPage />);
    await user.upload(screen.getByLabelText("Foto frontal do veículo"), photo());
    await user.click(screen.getByRole("button", { name: "Emitir ticket" }));
    await screen.findByText("9f2c4e1a");
    return { user };
  }

  const parked: Session = { ...session, plate_status: "READ", license_plate: "ABC1D23" };
  const offlineText = "Não foi possível consultar o servidor. Tentando novamente...";

  it("continua consultando após erro transitório e exibe a placa", async () => {
    mockedCreateEntry.mockResolvedValue(session);
    mockedGetSession
      .mockRejectedValueOnce(new ApiError(502, "Bad Gateway"))
      .mockRejectedValueOnce(new ApiError(0, "rede"))
      .mockResolvedValue(parked);
    await emitTicket();

    expect(await screen.findByText(offlineText)).toBeInTheDocument();
    await advance(4000);

    expect(screen.getByText("ABC1D23")).toBeInTheDocument();
    expect(screen.queryByText(offlineText)).not.toBeInTheDocument();
    expect(mockedGetSession).toHaveBeenCalledTimes(3);
  });

  it("após 60 s passa a consultar a cada 15 s e ainda detecta a placa", async () => {
    mockedCreateEntry.mockResolvedValue(session);
    mockedGetSession.mockResolvedValue(session);
    await emitTicket();

    await advance(60_000);
    const callsAtOneMinute = mockedGetSession.mock.calls.length;
    expect(callsAtOneMinute).toBeGreaterThanOrEqual(30);
    expect(screen.getByText(/A leitura da placa está demorando/)).toBeInTheDocument();

    await advance(30_000);
    // Fase lenta: ~2 consultas em 30 s (a troca de intervalo dispara uma imediata).
    expect(mockedGetSession.mock.calls.length - callsAtOneMinute).toBeLessThanOrEqual(3);

    mockedGetSession.mockResolvedValue(parked);
    await advance(15_000);
    expect(screen.getByText("ABC1D23")).toBeInTheDocument();
  });

  it("placa dada como ilegível na fase lenta encerra a consulta", async () => {
    mockedCreateEntry.mockResolvedValue(session);
    mockedGetSession.mockResolvedValue(session);
    await emitTicket();

    await advance(9 * 60_000);
    mockedGetSession.mockResolvedValue({ ...session, plate_status: "UNREADABLE" });
    await advance(15_000);

    expect(screen.getByText(/Não foi possível ler a placa pela foto/)).toBeInTheDocument();
    const calls = mockedGetSession.mock.calls.length;
    await advance(60_000);
    expect(mockedGetSession).toHaveBeenCalledTimes(calls);
  });

  it("carro que paga antes da leitura da placa segue sendo acompanhado até a placa sair", async () => {
    mockedCreateEntry.mockResolvedValue(session);
    mockedGetSession.mockResolvedValue({ ...session, status: "PAID" });
    await emitTicket();

    await advance(4000);
    expect(screen.getByText("Pago")).toBeInTheDocument();
    const calls = mockedGetSession.mock.calls.length;
    expect(calls).toBeGreaterThanOrEqual(2);

    mockedGetSession.mockResolvedValue({ ...parked, status: "PAID" });
    await advance(2000);
    expect(screen.getByText("ABC1D23")).toBeInTheDocument();
  });

  it("encerra no prazo com aviso e Tentar novamente retoma a consulta", async () => {
    mockedCreateEntry.mockResolvedValue(session);
    mockedGetSession.mockResolvedValue(session);
    const { user } = await emitTicket();

    await advance(12 * 60_000);
    expect(
      screen.getByText("A leitura da placa não foi concluída. O ticket é válido para a saída."),
    ).toBeInTheDocument();
    const calls = mockedGetSession.mock.calls.length;
    await advance(60_000);
    expect(mockedGetSession).toHaveBeenCalledTimes(calls);

    mockedGetSession.mockResolvedValue(parked);
    await user.click(screen.getByRole("button", { name: "Tentar novamente" }));
    await advance(1000);
    expect(screen.getByText("ABC1D23")).toBeInTheDocument();
  });

  it("erro definitivo encerra com mensagem e permite tentar novamente", async () => {
    mockedCreateEntry.mockResolvedValue(session);
    mockedGetSession.mockRejectedValue(new ApiError(404, "session not found"));
    const { user } = await emitTicket();

    expect(await screen.findByRole("alert")).toHaveTextContent("Sessão não encontrada.");
    await advance(10_000);
    expect(mockedGetSession).toHaveBeenCalledTimes(1);

    mockedGetSession.mockResolvedValue(parked);
    await user.click(screen.getByRole("button", { name: "Tentar novamente" }));
    await advance(1000);
    expect(screen.getByText("ABC1D23")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
