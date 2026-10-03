import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../services/api";
import { usePolling } from "./usePolling";

let hidden = false;

beforeEach(() => {
  vi.useFakeTimers();
  hidden = false;
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
});

afterEach(() => {
  vi.useRealTimers();
});

// Deixa as promises pendentes resolverem e os efeitos do React rodarem.
async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("usePolling", () => {
  it("chama o fetcher ao montar e de novo após intervalMs", async () => {
    const fetcher = vi.fn().mockResolvedValue(1);
    const { result } = renderHook(() => usePolling(fetcher, 1000));

    await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current.data).toBe(1);
    expect(result.current.loading).toBe(false);
    expect(result.current.lastUpdated).toBeInstanceOf(Date);

    await flush(1000);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("não sobrepõe chamadas quando o fetcher é lento", async () => {
    let resolve: (value: number) => void = () => {};
    const fetcher = vi.fn(
      () =>
        new Promise<number>((r) => {
          resolve = r;
        }),
    );
    renderHook(() => usePolling(fetcher, 1000));

    await flush(5000);
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => resolve(1));
    await flush(999);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await flush(1);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("mantém o data anterior quando a chamada seguinte falha", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(7)
      .mockRejectedValueOnce(new ApiError(500, "boom"))
      .mockResolvedValueOnce(8);
    const { result } = renderHook(() => usePolling(fetcher, 1000));

    await flush();
    expect(result.current.data).toBe(7);

    await flush(1000);
    expect(result.current.data).toBe(7);
    expect(result.current.error).toMatchObject({ status: 500, message: "boom" });

    await flush(1000);
    expect(result.current.data).toBe(8);
    expect(result.current.error).toBeUndefined();
  });

  it("para de chamar após o unmount", async () => {
    const fetcher = vi.fn().mockResolvedValue(1);
    const { unmount } = renderHook(() => usePolling(fetcher, 1000));

    await flush();
    unmount();
    await flush(5000);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("enabled: false não chama o fetcher", async () => {
    const fetcher = vi.fn().mockResolvedValue(1);
    const { result } = renderHook(() => usePolling(fetcher, 1000, { enabled: false }));

    await flush(5000);
    expect(fetcher).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(false);
  });

  it("refresh dispara uma busca imediata", async () => {
    const fetcher = vi.fn().mockResolvedValue(1);
    const { result } = renderHook(() => usePolling(fetcher, 10_000));

    await flush();
    act(() => result.current.refresh());
    await flush();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("pula a execução com a aba oculta e retoma no visibilitychange", async () => {
    const fetcher = vi.fn().mockResolvedValue(1);
    renderHook(() => usePolling(fetcher, 1000));
    await flush();

    hidden = true;
    await flush(5000);
    expect(fetcher).toHaveBeenCalledTimes(1);

    hidden = false;
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await flush();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
