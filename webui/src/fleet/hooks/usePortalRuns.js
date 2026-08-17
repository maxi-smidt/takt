import { useCallback, useEffect, useState } from "react";
import { request } from "../services/fleetService";
import { isSessionExpired, translatePortalError } from "../formatters";

export function usePortalRuns({ session, refreshSession }) {
  const [devices, setDevices] = useState([]);
  const [deviceId, setDeviceId] = useState("");
  const [runs, setRuns] = useState(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [error, setError] = useState("");

  const loadDevices = useCallback(async () => {
    try {
      const result = await request("/api/portal/devices");
      setDevices(result.devices || []);
      setDeviceId((current) => current || result.devices?.[0]?.id || "");
      setError("");
    } catch (failure) {
      if (isSessionExpired(failure)) {
        await refreshSession();
        return;
      }
      setError(translatePortalError(failure.message));
    }
  }, [refreshSession]);
  const loadRuns = useCallback(async () => {
    if (!deviceId) return;
    try {
      const query = new URLSearchParams();
      if (from) query.set("from", from);
      if (to) query.set("to", to);
      setRuns(await request("/api/portal/devices/" + deviceId + "/runs?" + query));
      setError("");
    } catch (failure) {
      if (isSessionExpired(failure)) {
        await refreshSession();
        return;
      }
      setError(translatePortalError(failure.message));
    }
  }, [deviceId, from, to, refreshSession]);
  useEffect(() => { queueMicrotask(loadDevices); }, [loadDevices]);
  useEffect(() => { queueMicrotask(loadRuns); }, [loadRuns]);

  const logout = async () => {
    await request("/api/session", { method: "DELETE" }, session.csrf_token);
    await refreshSession();
  };

  const command = async (run, operation, desired) => {
    if (!window.confirm(operation === "delete"
      ? "Diesen gespeicherten Lauf endgültig löschen?"
      : "Diese Korrektur übernehmen?")) return;
    try {
      await request(
        "/api/portal/devices/" + deviceId + "/runs/" + run.id + "/commands",
        {
          method: "POST",
          body: JSON.stringify({
            confirmed: true,
            operation,
            desired_added_time_ms: desired,
            expected_updated_at: run.updated_at,
            mirror_sha256: runs.mirror.sha256,
          }),
        },
        session.csrf_token,
      );
      setTimeout(loadRuns, 1000);
    } catch (failure) {
      setError(translatePortalError(failure.message));
    }
  };

  return {
    devices,
    deviceId,
    setDeviceId,
    runs,
    from,
    setFrom,
    to,
    setTo,
    error,
    loadDevices,
    logout,
    command,
  };
}
