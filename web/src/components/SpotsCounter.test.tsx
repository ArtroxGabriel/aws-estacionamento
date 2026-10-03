import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import SpotsCounter from "./SpotsCounter";

describe("SpotsCounter", () => {
  it("exibe spinner no primeiro carregamento", () => {
    render(<SpotsCounter loading stale={false} />);
    expect(screen.getByText("Carregando...")).toBeInTheDocument();
  });

  it("usa âmbar para 1 a 5 vagas e verde acima de 5", () => {
    const { rerender } = render(<SpotsCounter value={5} loading={false} stale={false} />);
    expect(screen.getByText("5")).toHaveClass("text-amber-600");
    rerender(<SpotsCounter value={6} loading={false} stale={false} />);
    expect(screen.getByText("6")).toHaveClass("text-green-600");
  });

  it("mantém o último valor com aviso quando está desatualizado", () => {
    render(<SpotsCounter value={12} loading={false} stale />);
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("Sem conexão — exibindo último valor")).toBeInTheDocument();
  });
});
