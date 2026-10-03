import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  createEntry,
  errorMessage,
  getAvailableSpots,
  getSession,
  listAuditEvents,
  listSessions,
  payExit,
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

  it("getSession codifica o ID na URL", async () => {
    const spy = mockFetch(JSON.stringify({ id: "x", status: "PARKED" }));
    await getSession("x/y");
    expect(lastCall(spy).url).toBe("/api/sessions/x%2Fy");
  });

  it("listSessions usa PARKED por padrão e retorna a lista", async () => {
    const spy = mockFetch(JSON.stringify({ sessions: [{ id: "1", amount_due: 10 }] }));
    const sessions = await listSessions();
    expect(lastCall(spy).url).toBe("/api/sessions?status=PARKED");
    expect(sessions).toHaveLength(1);
  });

  it("listAuditEvents usa limit 100 por padrão", async () => {
    const spy = mockFetch(JSON.stringify({ events: [] }));
    await expect(listAuditEvents()).resolves.toEqual([]);
    expect(lastCall(spy).url).toBe("/api/audit?limit=100");
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
    await expect(getSession("x")).rejects.toMatchObject({
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
    [new ApiError(500, "boom"), "resource", "Erro no servidor. Tente novamente. (boom)"],
    [new Error("x"), "resource", "Erro inesperado. Tente novamente."],
  ] as const)("mapeia %o (%s)", (err, context, expected) => {
    expect(errorMessage(err, context)).toBe(expected);
  });
});
