import { DependencyList, useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "./api";

export interface AsyncData<T> {
  data: T | undefined;
  error: string | null;
  loading: boolean;
  /** Reload; `quiet` keeps the current data visible (no spinner). */
  reload: (quiet?: boolean) => Promise<void>;
  setData: (value: T) => void;
}

/** Load data from an async function, re-running when deps change. */
export function useAsync<T>(fn: () => Promise<T>, deps: DependencyList): AsyncData<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const stableFn = useCallback(fn, deps);

  const reload = useCallback(
    async (quiet = false) => {
      const mine = ++seq.current;
      if (!quiet) setLoading(true);
      try {
        const value = await stableFn();
        if (mine === seq.current) {
          setData(value);
          setError(null);
        }
      } catch (err) {
        if (mine === seq.current) setError(errorMessage(err));
      } finally {
        if (mine === seq.current) setLoading(false);
      }
    },
    [stableFn],
  );

  useEffect(() => {
    void reload();
  }, [reload]);

  return { data, error, loading, reload, setData };
}

/** Call `callback` every `ms` while `active` is true. */
export function useInterval(callback: () => void, ms: number, active: boolean): void {
  const saved = useRef(callback);
  useEffect(() => {
    saved.current = callback;
  }, [callback]);
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => saved.current(), ms);
    return () => window.clearInterval(id);
  }, [ms, active]);
}
