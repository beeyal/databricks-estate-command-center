import { useEffect, useState } from "react";

interface ApiState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

// Runs an async fetcher and re-runs whenever any dep changes. The fetcher is
// intentionally not in the dep array — deps drive re-fetch.
export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[]): ApiState<T> {
  const [state, setState] = useState<ApiState<T>>({ data: null, loading: true, error: null });
  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    fetcher()
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ data: null, loading: false, error: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}
