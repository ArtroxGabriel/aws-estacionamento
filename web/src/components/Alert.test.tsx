import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import Alert from "./Alert";

describe("Alert", () => {
  it("usa role=alert para erro", () => {
    render(<Alert variant="error">Falhou</Alert>);
    expect(screen.getByRole("alert")).toHaveTextContent("Falhou");
  });

  it.each(["success", "warning", "info"] as const)("usa role=status para %s", (variant) => {
    render(<Alert variant={variant}>Mensagem</Alert>);
    expect(screen.getByRole("status")).toHaveTextContent("Mensagem");
  });

  it("não exibe o botão sem onRetry", () => {
    render(<Alert variant="error">Falhou</Alert>);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("botão Tentar novamente chama onRetry", async () => {
    const onRetry = vi.fn();
    render(
      <Alert variant="error" onRetry={onRetry}>
        Falhou
      </Alert>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Tentar novamente" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
