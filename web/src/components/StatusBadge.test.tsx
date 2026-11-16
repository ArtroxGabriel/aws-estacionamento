import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import StatusBadge, { PlateStatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it.each([
    ["PARKED", "Estacionado"],
    ["PAID", "Pago"],
  ] as const)("exibe %s em português", (status, label) => {
    render(<StatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});

describe("PlateStatusBadge", () => {
  it.each([
    ["PENDING", "Lendo placa..."],
    ["READ", "Lida pela foto"],
    ["UNREADABLE", "Sem placa"],
    ["MANUAL", "Digitada no caixa"],
  ] as const)("exibe %s em português", (status, label) => {
    render(<PlateStatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("sessão sem placa não usa cor de erro: o ticket continua válido", () => {
    render(<PlateStatusBadge status="UNREADABLE" />);
    expect(screen.getByText("Sem placa").className).not.toMatch(/red/);
  });
});
