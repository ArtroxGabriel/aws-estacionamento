import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import StatusBadge from "./StatusBadge";

describe("StatusBadge", () => {
  it.each([
    ["PROCESSING", "Processando"],
    ["PARKED", "Estacionado"],
    ["PAID", "Pago"],
    ["FAILED", "Falha no OCR"],
  ] as const)("exibe %s em português", (status, label) => {
    render(<StatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});
