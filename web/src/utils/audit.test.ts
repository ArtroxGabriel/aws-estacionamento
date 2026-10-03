import { describe, expect, it } from "vitest";
import type { AuditEvent } from "../types/api";
import { describeDetails, describeEvent, plateOf, sessionInfo, sortByNewest } from "./audit";

describe("describeDetails", () => {
  it('monta "Pago · R$ 10,00" para EXIT_PAYMENT', () => {
    expect(describeDetails({ status: "PAID", amount_paid: 10 })).toMatch(/^Pago · R\$\s10,00$/);
  });

  it("traduz o status de ENTRY", () => {
    expect(describeDetails({ status: "PROCESSING", s3_photo_key: "photos/x.jpg" })).toBe(
      "Processando",
    );
  });

  it("exibe o motivo de POISON_MESSAGE", () => {
    expect(describeDetails({ session_id: "x", reason: "invalid json" })).toBe("invalid json");
  });

  it("ignora campos com tipo inesperado e status desconhecido aparece cru", () => {
    expect(describeDetails({ amount_paid: "10", reason: 3 })).toBe("");
    expect(describeDetails({ status: "ARCHIVED" })).toBe("ARCHIVED");
  });
});

describe("plateOf", () => {
  it("retorna a placa apenas quando é string", () => {
    expect(plateOf({ license_plate: "ABC1D23" })).toBe("ABC1D23");
    expect(plateOf({ license_plate: 123 })).toBeUndefined();
    expect(plateOf({})).toBeUndefined();
  });
});

describe("sortByNewest", () => {
  const event = (id: string, timestamp: string): AuditEvent => ({
    id,
    action: "ENTRY",
    entity_id: id,
    timestamp,
    details: {},
  });

  it("ordena do mais recente para o mais antigo", () => {
    const sorted = sortByNewest([
      event("a", "2026-10-03T14:05:09.5Z"),
      event("b", "2026-10-03T14:05:09.51Z"),
      event("c", "2026-10-03T14:05:10Z"),
    ]);
    expect(sorted.map((e) => e.id)).toEqual(["c", "b", "a"]);
  });
});

describe("sessionInfo", () => {
  it("combina placa/status das sessões com os eventos de OCR e de falha", () => {
    const info = sessionInfo(
      [
        { id: "a#1", action: "OCR_PROCESSING", entity_id: "a", timestamp: "", details: { license_plate: "AAA1A11" } },
        { id: "d#1", action: "POISON_MESSAGE", entity_id: "d", timestamp: "", details: { reason: "x" } },
      ],
      [
        { id: "b", license_plate: "BBB2B22", status: "PAID", s3_photo_key: "", entered_at: "" },
        { id: "c", status: "FAILED", s3_photo_key: "", entered_at: "" },
      ],
    );
    expect(info.get("a")).toEqual({ plate: "AAA1A11" });
    expect(info.get("b")).toEqual({ plate: "BBB2B22", status: "PAID" });
    expect(info.get("c")).toEqual({ plate: undefined, status: "FAILED" });
    expect(info.get("d")).toEqual({ status: "FAILED" });
  });
});

describe("describeEvent", () => {
  const entry = {
    id: "a#1",
    action: "ENTRY",
    entity_id: "a",
    timestamp: "",
    details: { status: "PROCESSING", s3_photo_key: "x" },
  };

  it("entrada exibe Processando enquanto o OCR não terminou", () => {
    expect(describeEvent(entry, undefined)).toBe("Processando");
    expect(describeEvent(entry, { status: "PROCESSING" })).toBe("Processando");
  });

  it("entrada exibe Processado quando a placa foi identificada", () => {
    expect(describeEvent(entry, { plate: "ABC1D23", status: "PARKED" })).toBe("Processado");
    expect(describeEvent(entry, { plate: "ABC1D23" })).toBe("Processado");
  });

  it("entrada exibe Falha no OCR quando a sessão terminou sem placa", () => {
    expect(describeEvent(entry, { status: "FAILED" })).toBe("Falha no OCR");
    expect(describeEvent(entry, { status: "PAID" })).toBe("Falha no OCR");
  });

  it("demais eventos usam os detalhes gravados", () => {
    const payment = { ...entry, action: "EXIT_PAYMENT", details: { status: "PAID", amount_paid: 10 } };
    expect(describeEvent(payment, { plate: "ABC1D23" })).toMatch(/^Pago · R\$\s10,00$/);
  });
});
