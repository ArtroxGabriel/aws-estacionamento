import { describe, expect, it } from "vitest";
import type { AuditEvent } from "../types/api";
import {
  describeDetails,
  describeEvent,
  failureReasonLabel,
  plateOf,
  sessionInfo,
  sortByNewest,
} from "./audit";

describe("describeDetails", () => {
  it('monta "Pago · R$ 10,00" para EXIT_PAYMENT', () => {
    expect(describeDetails({ status: "PAID", amount_paid: 10 })).toMatch(/^Pago · R\$\s10,00$/);
  });

  it("traduz o status de ENTRY", () => {
    expect(describeDetails({ status: "PROCESSING", s3_photo_key: "photos/x.jpg" })).toBe(
      "Processando",
    );
  });

  it("exibe o motivo de POISON_MESSAGE traduzido", () => {
    expect(describeDetails({ session_id: "x", reason: "unreadable plate: empty" })).toBe(
      "Placa ilegível (nenhum texto reconhecido)",
    );
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
        { id: "d#2", action: "OCR_FAILED", entity_id: "d", timestamp: "", details: { status: "FAILED", reason: "unreadable plate: empty" } },
        { id: "d#1", action: "POISON_MESSAGE", entity_id: "d", timestamp: "", details: { reason: "unreadable plate: no_match" } },
        { id: "e#1", action: "POISON_MESSAGE", entity_id: "e", timestamp: "", details: { reason: "S3 download failed: unavailable" } },
      ],
      [
        { id: "b", license_plate: "BBB2B22", status: "PAID", s3_photo_key: "", entered_at: "" },
        { id: "c", status: "FAILED", s3_photo_key: "", entered_at: "" },
      ],
    );
    expect(info.get("a")).toEqual({ plate: "AAA1A11" });
    expect(info.get("b")).toEqual({ plate: "BBB2B22", status: "PAID" });
    expect(info.get("c")).toEqual({ plate: undefined, status: "FAILED" });
    // Motivo do evento mais recente (a lista vem do mais novo para o mais antigo).
    expect(info.get("d")).toEqual({ status: "FAILED", failure: "Placa ilegível (nenhum texto reconhecido)" });
    // POISON_MESSAGE sozinho não encerra a sessão.
    expect(info.get("e")).toEqual({ failure: "Falha ao baixar a foto (S3 indisponível)" });
  });
});

describe("failureReasonLabel", () => {
  it.each([
    ["unreadable plate: empty", "Placa ilegível (nenhum texto reconhecido)"],
    ["unreadable plate: no_match", "Placa ilegível (texto fora do padrão de placa)"],
    ["OCR failed: tesseract not found", "Erro no OCR: tesseract not found"],
    ["S3 download failed: not_found", "Falha ao baixar a foto (foto não encontrada no S3)"],
    ["S3 download failed", "Falha ao baixar a foto"],
    ["session lookup failed (RDS)", "Falha ao consultar a sessão (RDS)"],
    ["unexpected session status PAID", "Status inesperado da sessão: PAID"],
    ["RDS commit failed", "Falha ao gravar o resultado (RDS commit)"],
    ["Redis failed", "Falha ao gravar o resultado (Redis)"],
    ["invalid JSON body", "Mensagem inválida na fila"],
    ["algo novo", "algo novo"],
  ])("%s → %s", (reason, label) => {
    expect(failureReasonLabel(reason)).toBe(label);
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

  it("entrada exibe Estacionado quando a placa foi identificada", () => {
    expect(describeEvent(entry, { plate: "ABC1D23", status: "PARKED" })).toBe("Estacionado");
    expect(describeEvent(entry, { plate: "ABC1D23" })).toBe("Estacionado");
    // Mostra o estado ao qual a entrada levou, não o atual: já pago continua Estacionado.
    expect(describeEvent(entry, { plate: "ABC1D23", status: "PAID" })).toBe("Estacionado");
  });

  it("entrada exibe Falha no OCR quando a sessão terminou sem placa", () => {
    expect(describeEvent(entry, { status: "FAILED" })).toBe("Falha no OCR");
    expect(describeEvent(entry, { status: "PAID" })).toBe("Falha no OCR");
  });

  it("entrada com falha exibe o motivo", () => {
    expect(describeEvent(entry, { status: "FAILED", failure: "Placa ilegível" })).toBe(
      "Falha no OCR · Placa ilegível",
    );
  });

  it("entrada ainda PROCESSING com falha de infraestrutura exibe Erro no processamento", () => {
    expect(describeEvent(entry, { status: "PROCESSING", failure: "Falha ao baixar a foto" })).toBe(
      "Erro no processamento · Falha ao baixar a foto",
    );
  });

  it("eventos de falha exibem só o motivo traduzido", () => {
    const failed = { ...entry, action: "OCR_FAILED", details: { status: "FAILED", reason: "unreadable plate: no_match" } };
    expect(describeEvent(failed, undefined)).toBe("Placa ilegível (texto fora do padrão de placa)");
    const entryFailed = { ...entry, action: "ENTRY_FAILED", details: { status: "FAILED", error: "queue down" } };
    expect(describeEvent(entryFailed, undefined)).toBe("queue down");
  });

  it("demais eventos usam os detalhes gravados", () => {
    const payment = { ...entry, action: "EXIT_PAYMENT", details: { status: "PAID", amount_paid: 10 } };
    expect(describeEvent(payment, { plate: "ABC1D23" })).toMatch(/^Pago · R\$\s10,00$/);
  });
});
