import { useCallback, useEffect, useState } from "react";
import { request } from "../services/fleetService";

export function useSession() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const refreshSession = useCallback(async () => {
    const result = await request("/api/session");
    setSession(result.authenticated ? result : null);
    setLoading(false);
  }, []);
  useEffect(() => { queueMicrotask(refreshSession); }, [refreshSession]);
  return { session, loading, refreshSession };
}
