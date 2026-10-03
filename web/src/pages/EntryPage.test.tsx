import { render, screen } from "@testing-library/react";
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

const session: Session = {
  id: "9f2c4e1a7b3d4c5e6f708192a3b4c5d6",
  status: "PROCESSING",
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

  it("envio bem-sucedido exibe o ticket com ID curto e status Processando", async () => {
    mockedCreateEntry.mockResolvedValue(session);
    mockedGetSession.mockReturnValue(new Promise(() => {}));
    await submitPhoto();

    expect(await screen.findByText("9f2c4e1a")).toBeInTheDocument();
    expect(screen.getByText(session.id)).toBeInTheDocument();
    expect(screen.getByText("Processando")).toBeInTheDocument();
    expect(screen.getByText("Cancela liberada. A placa está sendo identificada...")).toBeInTheDocument();
    expect(mockedGetSession).toHaveBeenCalledWith(session.id);
  });

  it("exibe a placa quando o OCR conclui (PARKED)", async () => {
    mockedCreateEntry.mockResolvedValue(session);
    mockedGetSession.mockResolvedValue({ ...session, status: "PARKED", license_plate: "ABC1D23" });
    await submitPhoto();

    expect(await screen.findByText("ABC1D23")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Placa identificada: ABC1D23");
    expect(screen.getByText("Estacionado")).toBeInTheDocument();
  });

  it("exibe aviso para procurar o operador quando o OCR falha", async () => {
    mockedCreateEntry.mockResolvedValue(session);
    mockedGetSession.mockResolvedValue({ ...session, status: "FAILED" });
    await submitPhoto();

    expect(
      await screen.findByText("Não foi possível ler a placa. Procure o operador."),
    ).toBeInTheDocument();
  });

  it("erro na consulta do OCR mantém o ticket como Processando", async () => {
    mockedCreateEntry.mockResolvedValue(session);
    mockedGetSession.mockRejectedValue(new ApiError(500, "boom"));
    await submitPhoto();

    expect(await screen.findByText("9f2c4e1a")).toBeInTheDocument();
    expect(screen.getByText("Processando")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
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
