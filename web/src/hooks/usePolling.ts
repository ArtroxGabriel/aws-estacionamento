import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../services/api";

export interface PollingResult<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  lastUpdated: Date | undefined;
  refresh: () => void;
}

function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  return new ApiError(0, err instanceof Error ? err.message : String(err));
}

// Busca imediatamente e depois a cada `intervalMs`, encadeando um setTimeout após cada
// resposta (nunca sobrepõe requisições). `fetcher` deve ser estável (useCallback ou
// função de módulo), senão o polling reinicia a cada render.
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  options: { enabled?: boolean } = {},
): PollingResult<T> {
  const enabled = options.enabled ?? true;
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError>();
  const [loading, setLoading] = useState(enabled);
  const [lastUpdated, setLastUpdated] = useState<Date>();
  const refreshRef = useRef<() => void>(() => {});

  useEffect(() => {
    if (!enabled) return;

    let active = true;
    let inFlight = false;
    let refreshPending = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const clearTimer = () => {
      clearTimeout(timer);
      timer = undefined;
    };

    const run = async () => {
      clearTimer();
      // Aba em segundo plano: não busca nem agenda; o visibilitychange retoma.
      if (document.hidden) return;

      inFlight = true;
      setLoading(true);
      try {
        const result = await fetcher();
        if (!active) return;
        setData(result);
        setError(undefined);
        setLastUpdated(new Date());
      } catch (err) {
        if (!active) return;
        setError(toApiError(err));
      } finally {
        inFlight = false;
        if (active) {
          setLoading(false);
          if (refreshPending) {
            refreshPending = false;
            void run();
          } else {
            timer = setTimeout(run, intervalMs);
          }
        }
      }
    };

    refreshRef.current = () => {
      if (inFlight) refreshPending = true;
      else void run();
    };

    const onVisibilityChange = () => {
      if (!document.hidden && !inFlight && timer === undefined) void run();
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    void run();

    return () => {
      active = false;
      clearTimer();
      refreshRef.current = () => {};
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [fetcher, intervalMs, enabled]);

  const refresh = useCallback(() => refreshRef.current(), []);

  return { data, error, loading: enabled && loading, lastUpdated, refresh };
}
