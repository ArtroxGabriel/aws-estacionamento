import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  createEntry,
  errorMessage,
  getAvailableSpots,
  isTransientError,
  getSession,
  listAuditEvents,
  listSessions,
  payExit,
  updatePlate,
  deleteSession,
} from "./api";

function mockFetch(body: string, init?: ResponseInit) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(body, init));
}

function lastCall(spy: ReturnType<typeof mockFetch>) {
  const [url, init] = spy.mock.calls[0];
  return { url: String(url), init };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("services/api", () => {
  it("getAvailableSpots retorna available_spots", async () => {
    const spy = mockFetch(JSON.stringify({ available_spots: 42 }));
    await expect(getAvailableSpots()).resolves.toBe(42);
    expect(lastCall(spy).url).toBe("/api/spots/available");
  });

  it("createEntry envia POST multipart com o campo photo e sem Content-Type", async () => {
    const spy = mockFetch(JSON.stringify({ id: "abc", status: "PROCESSING" }), { status: 201 });
    const photo = new File(["img"], "car.jpg", { type: "image/jpeg" });

    const session = await createEntry(photo);

    expect(session.id).toBe("abc");
    const { url, init } = lastCall(spy);
    expect(url).toBe("/api/entries");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeInstanceOf(FormData);
    const form = init?.body;
    expect(form instanceof FormData && form.get("photo")).toBe(photo);
    expect(new Headers(init?.headers).has("Content-Type")).toBe(false);
  });

  it("payExit usa POST /exits/<id>/pay com o ID codificado", async () => {
    const spy = mockFetch(JSON.stringify({ id: "a/b", status: "PAID" }));
    await payExit("a/b c");
    const { url, init } = lastCall(spy);
    expect(url).toBe("/api/exits/a%2Fb%20c/pay");
    expect(init?.method).toBe("POST");
  });

  it("listSessions usa PARKED por padrão e retorna a lista crua", async () => {
    const spy = mockFetch(JSON.stringify([{ id: "1", status: "PARKED" }]));
    const sessions = await listSessions();
    expect(lastCall(spy).url).toBe("/api/sessions?status=PARKED");
    expect(sessions).toHaveLength(1);
  });

  it("listSessions converte null (lista vazia na API) em []", async () => {
    mockFetch("null\n");
    await expect(listSessions("FAILED")).resolves.toEqual([]);
  });

  it("getSession localiza a sessão na listagem com status=ALL", async () => {
    const spy = mockFetch(
      JSON.stringify([
        { id: "a", status: "PAID" },
        { id: "b", status: "PARKED", license_plate: "ABC1D23" },
      ]),
    );
    await expect(getSession("b")).resolves.toMatchObject({ license_plate: "ABC1D23" });
    expect(lastCall(spy).url).toBe("/api/sessions?status=ALL");
  });

  it("getSession lança ApiError 404 quando a sessão não está na lista", async () => {
    mockFetch(JSON.stringify([{ id: "a", status: "PAID" }]));
    await expect(getSession("z")).rejects.toMatchObject({ status: 404 });
  });

  it("listAuditEvents retorna a lista crua e converte null em []", async () => {
    const spy = mockFetch(JSON.stringify([{ id: "1#1", action: "ENTRY" }]));
    await expect(listAuditEvents()).resolves.toHaveLength(1);
    expect(lastCall(spy).url).toBe("/api/audit");

    mockFetch("null");
    await expect(listAuditEvents()).resolves.toEqual([]);
  });

  it("erro 404 com corpo JSON lança ApiError com status e mensagem", async () => {
    mockFetch('{"error":"session not found"}\n', {
      status: 404,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
    const err = await payExit("nope").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 404, message: "session not found" });
  });

  it("erro com corpo não-JSON usa o texto como mensagem", async () => {
    mockFetch("404 page not found\n", { status: 404 });
    await expect(payExit("x")).rejects.toMatchObject({
      status: 404,
      message: "404 page not found",
    });
  });

  it("falha de rede lança ApiError com status 0", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(getAvailableSpots()).rejects.toMatchObject({
      status: 0,
      message: "Não foi possível conectar à API.",
    });
  });
});

describe("errorMessage", () => {
  it.each([
    [new ApiError(0, "x"), "resource", "Não foi possível conectar à API."],
    [new ApiError(400, "photo is required"), "resource", "photo is required"],
    [new ApiError(404, "session not found"), "session", "Sessão não encontrada."],
    [new ApiError(404, "not found"), "resource", "Recurso não encontrado."],
    [new ApiError(409, "conflict"), "session", "A sessão não está pronta para pagamento."],
    [new ApiError(413, "photo too large"), "resource", "A foto excede o tamanho máximo de 10 MB."],
    [new ApiError(500, "boom"), "resource", "Erro no servidor. Tente novamente. (boom)"],
    [new Error("x"), "resource", "Erro inesperado. Tente novamente."],
  ] as const)("mapeia %o (%s)", (err, context, expected) => {
    expect(errorMessage(err, context)).toBe(expected);
  });
});

describe("isTransientError", () => {
  it.each([
    [new ApiError(0, "rede"), true],
    [new ApiError(429, "too many"), true],
    [new ApiError(502, "bad gateway"), true],
    [new ApiError(503, "unavailable"), true],
    [new ApiError(400, "bad request"), false],
    [new ApiError(404, "not found"), false],
    [new Error("x"), false],
  ] as const)("%o → %s", (err, expected) => {
    expect(isTransientError(err)).toBe(expected);
  });
});

describe("services/api — placa e exclusão", () => {
  it("updatePlate usa PATCH /sessions/<id> com JSON", async () => {
    const spy = mockFetch(JSON.stringify({ id: "s1", status: "PARKED", license_plate: "ABC1D23" }));
    const session = await updatePlate("s1", "ABC1D23");
    const { url, init } = lastCall(spy);
    expect(session.license_plate).toBe("ABC1D23");
    expect(url).toBe("/api/sessions/s1");
    expect(init?.method).toBe("PATCH");
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    expect(init?.body).toBe(JSON.stringify({ license_plate: "ABC1D23" }));
  });

  it("deleteSession usa DELETE /sessions/<id> com o ID codificado", async () => {
    const spy = mockFetch(JSON.stringify({ id: "a b", status: "PARKED" }));
    await deleteSession("a b");
    const { url, init } = lastCall(spy);
    expect(url).toBe("/api/sessions/a%20b");
    expect(init?.method).toBe("DELETE");
  });

  it("errorMessage no contexto plate traduz 400 e 409", () => {
    expect(errorMessage(new ApiError(400, "invalid license plate"), "plate")).toMatch(/Placa inválida/);
    expect(errorMessage(new ApiError(409, "x"), "plate")).toMatch(/Só é possível alterar a placa/);
  });
});
