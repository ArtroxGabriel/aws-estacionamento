import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, getAvailableSpots } from "../services/api";
import DashboardPage from "./DashboardPage";

vi.mock("../services/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/api")>()),
  getAvailableSpots: vi.fn(),
}));

const mockedGetAvailableSpots = vi.mocked(getAvailableSpots);

function renderPage() {
  return render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.resetAllMocks();
});

describe("DashboardPage", () => {
  it("exibe o número de vagas retornado", async () => {
    mockedGetAvailableSpots.mockResolvedValue(42);
    renderPage();
    expect(await screen.findByText("42")).toBeInTheDocument();
    expect(screen.getByText(/Atualizado às \d{2}:\d{2}:\d{2}/)).toBeInTheDocument();
  });

  it("exibe LOTADO quando não há vagas", async () => {
    mockedGetAvailableSpots.mockResolvedValue(0);
    renderPage();
    expect(await screen.findByText("LOTADO")).toBeInTheDocument();
  });

  it("em erro sem dado exibe Alert e Tentar novamente chama a API de novo", async () => {
    mockedGetAvailableSpots
      .mockRejectedValueOnce(new ApiError(0, "Não foi possível conectar à API."))
      .mockResolvedValueOnce(10);
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("Não foi possível conectar à API.");
    await userEvent.click(screen.getByRole("button", { name: "Tentar novamente" }));

    expect(await screen.findByText("10")).toBeInTheDocument();
    expect(mockedGetAvailableSpots).toHaveBeenCalledTimes(2);
  });

  it("tem links para entrada, saída e auditoria", () => {
    mockedGetAvailableSpots.mockResolvedValue(5);
    renderPage();
    expect(screen.getByRole("link", { name: /Simular entrada/ })).toHaveAttribute("href", "/entrada");
    expect(screen.getByRole("link", { name: /Processar pagamento/ })).toHaveAttribute("href", "/saida");
    expect(screen.getByRole("link", { name: /Auditoria/ })).toHaveAttribute("href", "/auditoria");
  });
});
