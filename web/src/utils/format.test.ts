import { describe, expect, it } from "vitest";
import {
  actionLabel,
  formatCurrency,
  formatDateTime,
  formatDuration,
  formatTime,
  isValidPlate,
  normalizePlate,
  shortId,
  statusLabel,
} from "./format";

describe("formatCurrency", () => {
  it("formata em reais (Intl pode usar espaço não separável)", () => {
    expect(formatCurrency(10)).toMatch(/^R\$\s10,00$/);
    expect(formatCurrency(1234.5)).toMatch(/^R\$\s1\.234,50$/);
  });
});

describe("formatDateTime / formatTime", () => {
  it("formata data e hora no fuso local", () => {
    const iso = new Date(2026, 9, 3, 14, 5, 9).toISOString();
    expect(formatDateTime(iso)).toBe("03/10/2026 14:05:09");
  });

  it("devolve o valor original para datas inválidas", () => {
    expect(formatDateTime("invalida")).toBe("invalida");
  });

  it("formatTime usa HH:MM:SS", () => {
    expect(formatTime(new Date(2026, 0, 1, 7, 3, 2))).toBe("07:03:02");
  });
});

describe("formatDuration", () => {
  const now = new Date("2026-10-03T16:00:00Z");
  const ago = (ms: number) => new Date(now.getTime() - ms).toISOString();

  it("menos de 1 minuto", () => {
    expect(formatDuration(ago(59_000), now)).toBe("< 1min");
    expect(formatDuration(ago(0), now)).toBe("< 1min");
  });

  it("minutos", () => {
    expect(formatDuration(ago(12 * 60_000), now)).toBe("12min");
  });

  it("exatamente 1 hora", () => {
    expect(formatDuration(ago(60 * 60_000), now)).toBe("1h 00min");
  });

  it("horas e minutos", () => {
    expect(formatDuration(ago(125 * 60_000), now)).toBe("2h 05min");
  });

  it("data inválida", () => {
    expect(formatDuration("invalida", now)).toBe("< 1min");
  });
});

describe("shortId", () => {
  it("retorna os 8 primeiros caracteres", () => {
    expect(shortId("9f2c4e1a7b3d")).toBe("9f2c4e1a");
    expect(shortId("abc")).toBe("abc");
  });
});

describe("normalizePlate", () => {
  it("remove hífen/espaços e converte para maiúsculas", () => {
    expect(normalizePlate("abc-1d23")).toBe("ABC1D23");
    expect(normalizePlate(" abc 1234 ")).toBe("ABC1234");
  });
});

describe("statusLabel", () => {
  it("traduz os status", () => {
    expect(statusLabel("PROCESSING")).toBe("Processando");
    expect(statusLabel("PARKED")).toBe("Estacionado");
    expect(statusLabel("PAID")).toBe("Pago");
    expect(statusLabel("FAILED")).toBe("Falha no OCR");
  });
});

describe("actionLabel", () => {
  it("traduz as ações conhecidas", () => {
    expect(actionLabel("ENTRY")).toBe("Entrada");
    expect(actionLabel("OCR_PROCESSING")).toBe("Leitura de placa");
    expect(actionLabel("EXIT_PAYMENT")).toBe("Pagamento/Saída");
    expect(actionLabel("POISON_MESSAGE")).toBe("Falha no processamento");
  });

  it("devolve a própria ação quando desconhecida", () => {
    expect(actionLabel("NEW_ACTION")).toBe("NEW_ACTION");
    expect(actionLabel("toString")).toBe("toString");
  });
});

describe("isValidPlate", () => {
  it("aceita Mercosul e formato antigo, com ou sem hífen e espaços", () => {
    expect(isValidPlate("ABC1D23")).toBe(true);
    expect(isValidPlate("abc-1234")).toBe(true);
    expect(isValidPlate(" lsn 4i49 ")).toBe(true);
  });

  it("recusa tamanhos e posições fora do padrão", () => {
    for (const plate of ["", "AB12345", "ABCD123", "1234567", "ABC12345", "ABC1DD3"]) {
      expect(isValidPlate(plate)).toBe(false);
    }
  });
});
