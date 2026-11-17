import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, getAvailableSpots, listSessions } from "../services/api";
import DashboardPage from "./DashboardPage";

vi.mock("../services/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/api")>()),
  getAvailableSpots: vi.fn(),
  listSessions: vi.fn(),
}));

const mockedGetAvailableSpots = vi.mocked(getAvailableSpots);
const mockedListSessions = vi.mocked(listSessions);

function renderPage() {
  if (!mockedListSessions.getMockImplementation()) mockedListSessions.mockResolvedValue([]);
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
    expect(screen.getByRole("link", { name: /Cancela de entrada/ })).toHaveAttribute("href", "/cancela-entrada");
    expect(screen.getByRole("link", { name: /^Pagamento/ })).toHaveAttribute("href", "/pagamento");
    expect(screen.getByRole("link", { name: /Cancela de saída/ })).toHaveAttribute("href", "/cancela-saida");
    expect(screen.getByRole("link", { name: /Auditoria/ })).toHaveAttribute("href", "/auditoria");
  });

  it("lista os veículos no pátio e distingue os já pagos", async () => {
    mockedGetAvailableSpots.mockResolvedValue(47);
    mockedListSessions.mockResolvedValue([
      { id: "aaaa1111bbbb2222", status: "PARKED", plate_status: "READ", license_plate: "ABC1D23", s3_photo_key: "a", entered_at: "2026-11-17T10:00:00Z" },
      { id: "cccc3333dddd4444", status: "PAID", plate_status: "UNREADABLE", s3_photo_key: "c", entered_at: "2026-11-17T09:00:00Z" },
      { id: "eeee5555ffff6666", status: "EXITED", plate_status: "READ", s3_photo_key: "e", entered_at: "2026-11-17T08:00:00Z" },
    ]);
    renderPage();

    expect(await screen.findByText("aaaa1111")).toBeInTheDocument();
    expect(screen.getByText("cccc3333")).toBeInTheDocument();
    expect(screen.queryByText("eeee5555")).not.toBeInTheDocument();
    expect(screen.getByText("Pago")).toBeInTheDocument();
    expect(screen.getByText("Estacionado")).toBeInTheDocument();
    expect(screen.getByText(/2 no pátio · 1 já pago,/)).toBeInTheDocument();
  });
});
