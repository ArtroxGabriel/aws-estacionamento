import { describe, expect, it } from "vitest";
import type { AuditEvent } from "../types/api";
import { describeDetails, plateOf, sortByNewest } from "./audit";

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
