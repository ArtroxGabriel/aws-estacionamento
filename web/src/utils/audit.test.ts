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
    expect(describeDetails({ status: "PARKED", s3_photo_key: "photos/x.jpg" })).toBe(
      "Estacionado",
    );
  });

  it("eventos antigos com status que não existem mais continuam legíveis", () => {
    expect(describeDetails({ status: "PROCESSING" })).toBe("Processando");
    expect(describeDetails({ status: "FAILED" })).toBe("Falha no OCR");
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
  it("combina placa/status das sessões com os eventos de leitura e de falha", () => {
    const info = sessionInfo(
      [
        { id: "a#1", action: "OCR_PROCESSING", entity_id: "a", timestamp: "", details: { license_plate: "AAA1A11" } },
        { id: "d#2", action: "OCR_FAILED", entity_id: "d", timestamp: "", details: { plate_status: "UNREADABLE", reason: "unreadable plate: empty" } },
        { id: "d#1", action: "POISON_MESSAGE", entity_id: "d", timestamp: "", details: { reason: "unreadable plate: no_match" } },
        { id: "e#1", action: "POISON_MESSAGE", entity_id: "e", timestamp: "", details: { reason: "S3 download failed: unavailable" } },
      ],
      [
        { id: "b", license_plate: "BBB2B22", status: "PAID", plate_status: "READ", s3_photo_key: "", entered_at: "" },
        { id: "c", status: "PARKED", plate_status: "UNREADABLE", s3_photo_key: "", entered_at: "" },
      ],
    );
    // Sessão já excluída: só os eventos falam dela, e não há foto para abrir.
    expect(info.get("a")).toEqual({ plate: "AAA1A11", plateStatus: "READ" });
    expect(info.get("b")).toEqual({ plate: "BBB2B22", status: "PAID", plateStatus: "READ", hasPhoto: true });
    // Sem placa, mas estacionada: nada de "falha" no status da sessão.
    expect(info.get("c")).toEqual({ plate: undefined, status: "PARKED", plateStatus: "UNREADABLE", hasPhoto: true });
    // Motivo do evento mais recente (a lista vem do mais novo para o mais antigo).
    expect(info.get("d")).toEqual({ plateStatus: "UNREADABLE", failure: "Placa ilegível (nenhum texto reconhecido)" });
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
    details: { status: "PARKED", s3_photo_key: "x", ocr_queued: true },
  };

  it("saída pela cancela mostra o método e a placa", () => {
    const exit = { ...entry, action: "EXIT_BARRIER_OPENED" };
    expect(describeEvent({ ...exit, details: { status: "EXITED", method: "plate", license_plate: "ABC1D23" } }, undefined)).toBe(
      "Cancela aberta pela placa · ABC1D23",
    );
    expect(describeEvent({ ...exit, details: { status: "EXITED", method: "ticket", license_plate: null } }, undefined)).toBe(
      "Cancela aberta pelo ticket",
    );
  });

  it("entrada é sempre Estacionado: o ticket vale desde a emissão", () => {
    expect(describeEvent(entry, undefined)).toBe("Estacionado");
    expect(describeEvent(entry, { plate: "ABC1D23", status: "PARKED", plateStatus: "READ" })).toBe(
      "Estacionado",
    );
    // Mostra o que a entrada produziu, não o estado atual: já pago continua Estacionado.
    expect(describeEvent(entry, { plate: "ABC1D23", status: "PAID" })).toBe("Estacionado");
  });

  it("entrada com a placa ainda em leitura avisa, sem tratar como erro", () => {
    expect(describeEvent(entry, { status: "PARKED", plateStatus: "PENDING" })).toBe(
      "Estacionado · lendo placa",
    );
  });

  it("entrada cuja foto não deu placa continua Estacionado, com o motivo ao lado", () => {
    expect(describeEvent(entry, { status: "PARKED", plateStatus: "UNREADABLE" })).toBe(
      "Estacionado · sem placa",
    );
    expect(
      describeEvent(entry, { status: "PAID", plateStatus: "UNREADABLE", failure: "Placa ilegível" }),
    ).toBe("Estacionado · sem placa: Placa ilegível");
    expect(describeEvent(entry, { failure: "Falha ao baixar a foto" })).toBe(
      "Estacionado · sem placa: Falha ao baixar a foto",
    );
  });

  it("placa digitada no caixa depois de uma leitura falha não aparece como sem placa", () => {
    expect(
      describeEvent(entry, { plate: "ABC1D23", plateStatus: "MANUAL", failure: "Placa ilegível" }),
    ).toBe("Estacionado");
  });

  it("entrada com a fila fora do ar diz que a foto não foi enviada para leitura", () => {
    const unqueued = { ...entry, details: { status: "PARKED", ocr_queued: false, queue_error: "down" } };
    expect(describeEvent(unqueued, { status: "PARKED", plateStatus: "UNREADABLE" })).toBe(
      "Estacionado · foto não enviada para leitura (fila indisponível)",
    );
  });

  it("entrada antiga (gravada como PROCESSING) é lida pelo mesmo critério", () => {
    const legacy = { ...entry, details: { status: "PROCESSING", s3_photo_key: "x" } };
    expect(describeEvent(legacy, { plate: "ABC1D23" })).toBe("Estacionado");
  });

  it("eventos de falha exibem só o motivo traduzido", () => {
    const failed = { ...entry, action: "OCR_FAILED", details: { plate_status: "UNREADABLE", reason: "unreadable plate: no_match" } };
    expect(describeEvent(failed, undefined)).toBe("Placa ilegível (texto fora do padrão de placa)");
    const entryFailed = { ...entry, action: "ENTRY_FAILED", details: { status: "FAILED", error: "queue down" } };
    expect(describeEvent(entryFailed, undefined)).toBe("queue down");
  });

  it("demais eventos usam os detalhes gravados", () => {
    const payment = { ...entry, action: "EXIT_PAYMENT", details: { status: "PAID", amount_paid: 10 } };
    expect(describeEvent(payment, { plate: "ABC1D23" })).toMatch(/^Pago · R\$\s10,00$/);
  });
});

describe("describeEvent — placa manual e exclusão", () => {
  const base = { id: "s1#1", entity_id: "s1", timestamp: "2026-10-03T10:00:00Z" };

  it("PLATE_CORRECTION mostra a placa digitada e a anterior", () => {
    const typed: AuditEvent = { ...base, action: "PLATE_CORRECTION", details: { license_plate: "ABC1D23" } };
    const fixed: AuditEvent = {
      ...base,
      action: "PLATE_CORRECTION",
      details: { license_plate: "LSN4I49", previous_plate: "LSN4149" },
    };
    expect(describeEvent(typed, undefined)).toBe("ABC1D23 (digitada no caixa)");
    expect(describeEvent(fixed, undefined)).toBe("LSN4I49 (antes: LSN4149)");
  });

  it("SESSION_DELETE mostra o status que a sessão tinha", () => {
    const event: AuditEvent = { ...base, action: "SESSION_DELETE", details: { status: "PARKED" } };
    expect(describeEvent(event, undefined)).toMatch(/^Registro excluído · estava /);
  });

  it("SESSION_DELETE de evento antigo traduz o status que não existe mais", () => {
    const event: AuditEvent = { ...base, action: "SESSION_DELETE", details: { status: "FAILED" } };
    expect(describeEvent(event, undefined)).toBe("Registro excluído · estava Falha no OCR");
  });

  it("PLATE_CORRECTION vence a leitura que falhou: placa nova, digitada no caixa", () => {
    const events: AuditEvent[] = [
      { ...base, id: "s1#2", action: "PLATE_CORRECTION", details: { license_plate: "ABC1D23", status: "PARKED" } },
      { ...base, id: "s1#1", action: "OCR_FAILED", details: { reason: "unreadable plate: no_match" } },
    ];
    const info = sessionInfo(events, []).get("s1");
    expect(info?.plate).toBe("ABC1D23");
    expect(info?.plateStatus).toBe("MANUAL");
  });
});
