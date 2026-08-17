import { useCallback, useEffect, useRef, useState } from "react";
import { request } from "../services/fleetService";
import { isSessionExpired } from "../formatters";

export function useFleetDashboard({ session, refreshSession }) {
  const [devices, setDevices] = useState([]);
  const [releases, setReleases] = useState([]);
  const [bundledRelease, setBundledRelease] = useState(null);
  const [jobs, setJobs] = useState([]);
  const diagnosticsSignature = useRef(null);
  const [diagnostics, setDiagnostics] = useState({});
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [deviceData, releaseData, jobData] = await Promise.all([
        request("/api/devices"), request("/api/releases"), request("/api/jobs"),
      ]);
      setDevices(deviceData.devices);
      setReleases(releaseData.releases);
      setBundledRelease(releaseData.bundled_release || null);
      setJobs(jobData.jobs);
      const signature = [
        deviceData.devices.map((device) => device.id).join(","),
        jobData.jobs
          .filter((job) => job.action === "collect_diagnostics")
          .map((job) => `${job.id}:${job.status}`)
          .join(","),
      ].join("|");
      if (signature !== diagnosticsSignature.current) {
        diagnosticsSignature.current = signature;
        const bundles = await Promise.all(
          deviceData.devices.map((device) =>
            request(`/api/devices/${device.id}/diagnostics`)
              .then((data) => [device.id, data.diagnostics])
              .catch(() => [device.id, []]),
          ),
        );
        setDiagnostics(Object.fromEntries(bundles));
      }
      setError("");
    } catch (failure) {
      if (isSessionExpired(failure)) {
        await refreshSession();
        return;
      }
      setError(failure.message);
    }
  }, [refreshSession]);
  useEffect(() => {
    queueMicrotask(load);
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  const submitJob = async (device, action, payload = {}, override = false) => {
    try {
      await request(
        `/api/devices/${device.id}/jobs`,
        { method: "POST", body: JSON.stringify({ action, payload, override }) },
        session.csrf_token,
      );
      await load();
    } catch (failure) {
      setError(failure.message);
    }
  };
  const createJob = async (device, action, payload = {}) => {
    const labels = {
      install_release: "install the selected version",
      mirror_now: "mirror its database now",
    };
    if (!window.confirm(`${device.name}: ${labels[action]}?`)) return;
    await submitJob(device, action, payload);
  };
  const cancelJob = async (job) => {
    if (!window.confirm(`Cancel ${job.action.replaceAll("_", " ")}?`)) return;
    try {
      await request(`/api/jobs/${job.id}/cancel`, { method: "POST", body: JSON.stringify({}) }, session.csrf_token);
      await load();
    } catch (failure) {
      setError(failure.message);
    }
  };
  const retryJob = async (job) => {
    if (!window.confirm(`Retry ${job.action.replaceAll("_", " ")}?`)) return;
    try {
      await request(`/api/jobs/${job.id}/retry`, { method: "POST", body: JSON.stringify({ override: false }) }, session.csrf_token);
      await load();
    } catch (failure) {
      setError(failure.message);
    }
  };
  const forceClearJob = async (job) => {
    if (!window.confirm(
      `Force-clear ${job.action.replaceAll("_", " ")} on ${job.device_name}? `
      + "This marks the job as failed and unblocks the device's queue, even if the "
      + "outcome on the device is unknown.",
    )) return;
    try {
      await request(`/api/jobs/${job.id}/force-clear`, { method: "POST", body: JSON.stringify({}) }, session.csrf_token);
      await load();
    } catch (failure) {
      setError(failure.message);
    }
  };

  const deleteJob = async (job) => {
    if (!window.confirm(`Remove the ${job.action.replaceAll("_", " ")} entry for ${job.device_name}?`)) return;
    try {
      await request(`/api/jobs/${job.id}`, { method: "DELETE" }, session.csrf_token);
      await load();
    } catch (failure) {
      setError(failure.message);
    }
  };

  const acknowledgeRecovery = async (device) => {
    if (!window.confirm(`${device.name}: acknowledge the update recovery alert?`)) return;
    try {
      await request(
        `/api/devices/${device.id}/acknowledge-recovery`,
        { method: "POST", body: JSON.stringify({}) },
        session.csrf_token,
      );
      await load();
    } catch (failure) {
      setError(failure.message);
    }
  };

  const uninstallRelease = async (release) => {
    if (!window.confirm(
      `Uninstall release ${release.version}? The cached archive is deleted; the version `
      + "stays listed and can be redownloaded on demand.",
    )) return;
    try {
      await request(
        `/api/releases/${release.id}/uninstall`,
        { method: "POST", body: JSON.stringify({}) },
        session.csrf_token,
      );
      await load();
    } catch (failure) {
      setError(failure.message);
    }
  };

  const revokeDevice = async (device) => {
    if (!window.confirm(`${device.name}: permanently revoke this device credential?`)) return;
    try {
      await request(
        `/api/devices/${device.id}/revoke`,
        { method: "POST", body: JSON.stringify({}) },
        session.csrf_token,
      );
      await load();
    } catch (failure) {
      setError(failure.message);
    }
  };
  const logout = async () => {
    await request("/api/session", { method: "DELETE" }, session.csrf_token);
    await refreshSession();
  };

  const online = devices.filter((device) => device.online).length;
  const mirroredRuns = devices.reduce((sum, device) => sum + (device.run_count || 0), 0);
  const insecureLan = window.location.protocol === "http:"
    && !["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);

  return {
    devices,
    releases,
    bundledRelease,
    jobs,
    diagnostics,
    error,
    online,
    mirroredRuns,
    insecureLan,
    load,
    submitJob,
    createJob,
    cancelJob,
    retryJob,
    forceClearJob,
    deleteJob,
    acknowledgeRecovery,
    revokeDevice,
    uninstallRelease,
    logout,
  };
}
