import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

// As páginas buscam dados ao montar; aqui só interessa a navegação.
vi.mock("./services/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./services/api")>()),
  getAvailableSpots: vi.fn().mockResolvedValue(10),
  listSessions: vi.fn().mockResolvedValue([]),
  listAuditEvents: vi.fn().mockResolvedValue([]),
}));

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App", () => {
  it("renderiza o nome do sistema no cabeçalho", () => {
    renderAt("/");
    expect(screen.getByRole("link", { name: "Estacionamento" })).toBeInTheDocument();
  });

  it("navega pelos links do cabeçalho e destaca o link ativo", async () => {
    const user = userEvent.setup();
    renderAt("/");
    const nav = screen.getByRole("navigation", { name: "Navegação principal" });

    const cases = [
      { link: "Cancela de entrada", heading: "Totem de Entrada" },
      { link: "Pagamento", heading: "Caixa / Pagamento" },
      { link: "Cancela de saída", heading: "Cancela de Saída" },
      { link: "Auditoria", heading: "Auditoria" },
      { link: "Painel", heading: "Painel" },
    ];

    for (const { link, heading } of cases) {
      const anchor = within(nav).getByRole("link", { name: link });
      await user.click(anchor);
      expect(screen.getByRole("heading", { level: 1, name: heading })).toBeInTheDocument();
      expect(anchor).toHaveAttribute("aria-current", "page");
    }
  });

  it("redireciona os endereços antigos", () => {
    renderAt("/entrada");
    expect(screen.getByRole("heading", { level: 1, name: "Totem de Entrada" })).toBeInTheDocument();
  });

  it("redireciona /saida para o caixa", () => {
    renderAt("/saida");
    expect(screen.getByRole("heading", { level: 1, name: "Caixa / Pagamento" })).toBeInTheDocument();
  });

  it("exibe a página 404 para rotas desconhecidas", () => {
    renderAt("/rota-inexistente");
    expect(screen.getByRole("heading", { name: "Página não encontrada" })).toBeInTheDocument();
  });
});
