import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

// Polls /api/health so the shell can show honest connection status.
export function useBackendStatus(intervalMs = 5000) {
  const [status, setStatus] = useState("checking"); // checking | online | offline
  useEffect(() => {
    let alive = true;
    const check = () =>
      api("/health")
        .then(() => alive && setStatus("online"))
        .catch(() => alive && setStatus("offline"));
    check();
    const id = setInterval(check, intervalMs);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [intervalMs]);
  return status;
}

// Loads one endpoint once; `reload` re-fetches. Returns {data, error, loading, reload}.
export function useApi(path) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const load = useCallback(() => {
    setState((s) => ({ ...s, loading: true }));
    api(path)
      .then((data) => setState({ data, error: null, loading: false }))
      .catch((error) => setState({ data: null, error, loading: false }));
  }, [path]);
  useEffect(load, [load]);
  return { ...state, reload: load };
}
