import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import App from "./App";

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
      { link: "Entrada", heading: "Totem de Entrada" },
      { link: "Saída", heading: "Caixa / Saída" },
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

  it("exibe a página 404 para rotas desconhecidas", () => {
    renderAt("/rota-inexistente");
    expect(screen.getByRole("heading", { name: "Página não encontrada" })).toBeInTheDocument();
  });
});
