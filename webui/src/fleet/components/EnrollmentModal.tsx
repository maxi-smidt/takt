// @ts-nocheck
import { useEffect, useState } from "react";
import { Activity, Check, KeyRound, Plus, RotateCcw, X } from "lucide-react";
import { openDeploymentEvents, request } from "../services/fleetService";
import { deploymentTargetError, hostnameChangeError } from "../deploymentValidation.js";
import { preferredReleaseId } from "../releaseSelection.js";
import { insecureRemoteHttp } from "../formatters";
import { Modal } from "./Modal";

export function EnrollmentModal({ csrf, onClose, releases, onDone }) {
  const [fields, setFields] = useState({
    device_name: "Bahn 1",
    hostname: "",
    confirm_hostname_change: false,
    ssh_user: "",
    target: "raspberrypi.local",
    registry_url: window.location.origin,
    release_id: preferredReleaseId(releases),
    allow_insecure_http: false,
  });
  const [deployment, setDeployment] = useState(null);
  const [events, setEvents] = useState([]);
  const [credentials, setCredentials] = useState({
    ssh_password: "", ssh_private_key: "", ssh_key_passphrase: "", sudo_password: "",
  });
  const [replaceHostKey, setReplaceHostKey] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const [streamAfter, setStreamAfter] = useState(0);
  const update = (key, value) => setFields((current) => ({ ...current, [key]: value, ...(key === "hostname" ? { confirm_hostname_change: false } : {}) }));
  const validate = () => {
    if (!/^[A-Za-z0-9ÄÖÜäöüß._ -]{1,80}$/.test(fields.device_name.trim())) return "Device name is invalid.";
    const hostnameError = hostnameChangeError(fields.hostname, fields.confirm_hostname_change);
    if (hostnameError) return hostnameError;
    if (!/^[A-Za-z_][A-Za-z0-9_-]{0,31}$/.test(fields.ssh_user)) return "SSH user is invalid.";
    if (deploymentTargetError(fields.target)) return "Target is invalid.";
    if (!fields.release_id) return "Upload a Raspberry Pi release first.";
    try {
      const url = new URL(fields.registry_url);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return "Registry URL is invalid.";
    } catch {
      return "Registry URL is invalid.";
    }
    if (insecureRemoteHttp(fields.registry_url) && !fields.allow_insecure_http) return "HTTPS or explicit HTTP acknowledgement is required.";
    return "";
  };

  const post = async (path, body) => {
    setBusy(true);
    setError("");
    try {
      const result = await request(path, { method: "POST", body: JSON.stringify(body) }, csrf);
      setDeployment(result.deployment);
      return true;
    } catch (failure) {
      setError(failure.message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const create = async (event) => {
    event.preventDefault();
    const validationError = validate();
    if (validationError) return setError(validationError);
    const result = await post("/api/deployments", fields);
    if (result) setEvents([]);
  };

  useEffect(() => {
    if (!deployment?.id) return undefined;
    let active = true;
    const path = "/api/deployments/" + deployment.id;
    const load = () => request(path)
      .then((result) => {
        if (!active) return;
        setDeployment(result.deployment);
        setError("");
        if (["succeeded", "failed", "cancelled", "interrupted"].includes(result.deployment.status)) {
          source?.close();
        }
      })
      .catch((failure) => active && setError(failure.message));
    const source = openDeploymentEvents(
      deployment.id,
      streamAfter,
      (event) => {
        if (!active) return;
        setEvents((current) => [...current, event]);
        if (event.deployment) {
          setDeployment(event.deployment);
          setError("");
          if (["succeeded", "failed", "cancelled", "interrupted"].includes(event.deployment.status)) source.close();
        }
      },
      (failure) => { if (active) setError(failure.message); },
    );
    load();
    source.onerror = () => { if (active) load(); };
    return () => { active = false; source.close(); };
  }, [deployment?.id, streamAfter]);

  const confirmHostKey = () => post(
    "/api/deployments/" + deployment.id + "/host-key",
    { fingerprint: deployment.host_key_fingerprint, replace: replaceHostKey },
  );
  const submitCredentials = async (event) => {
    event.preventDefault();
    if (!credentials.ssh_password && !credentials.ssh_private_key) return setError("SSH password or private key is required.");
    const accepted = await post("/api/deployments/" + deployment.id + "/credentials", credentials);
    if (accepted) setCredentials({ ssh_password: "", ssh_private_key: "", ssh_key_passphrase: "", sudo_password: "" });
  };
  const cancel = () => post("/api/deployments/" + deployment.id + "/cancel", {});
  const retry = async () => {
    const after = events.at(-1)?.id || 0;
    if (await post("/api/deployments/" + deployment.id + "/retry", {})) {
      setEvents([]);
      setStreamAfter(after);
    }
  };

  return (
    <Modal title="DEPLOY A RASPBERRY PI" eyebrow="GUIDED FIRST DEPLOYMENT" onClose={onClose} wide>
      {!deployment ? (
        <form className="modal-body" onSubmit={create}>
          <p>The registry checks, installs, enrolls, and verifies the Pi without a laptop checkout.</p>
          <div className="enrollment-fields">
            {[
              ["device_name", "DEVICE DISPLAY NAME", "Bahn 1"],
              ["hostname", "NEW SYSTEM HOSTNAME (OPTIONAL)", "Leave empty to preserve the Pi hostname"],
              ["ssh_user", "SSH USER", "pi"],
              ["target", "SSH ADDRESS / CURRENT HOSTNAME", "raspberrypi.local"],
              ["registry_url", "REGISTRY URL REACHABLE BY THE PI", "https://registry.example"],
            ].map(([key, label, placeholder]) => (
              <label className="field-label" key={key}>{label}
                <input value={fields[key]} placeholder={placeholder} onChange={(event) => update(key, event.target.value)} required={key !== "hostname"} />
              </label>
            ))}
            {fields.hostname && (
              <label className="insecure-opt-in">
                <input type="checkbox" checked={fields.confirm_hostname_change} onChange={(event) => update("confirm_hostname_change", event.target.checked)} />
                <span><strong>CONFIRM HOSTNAME CHANGE</strong> Preview: the Pi will move to <code>{fields.hostname}.local</code>; mDNS, DHCP, SSH host keys, and reconnect behavior can change.</span>
              </label>
            )}
            <label className="field-label">RASPBERRY PI RELEASE
              <select value={fields.release_id} onChange={(event) => update("release_id", event.target.value)} required>
                <option value="">SELECT A RELEASE</option>
                {releases.map((release) => <option value={release.id} key={release.id}>{release.version}{release.source === "bundled" ? " · VERIFIED" : ""}</option>)}
              </select>
            </label>
            {insecureRemoteHttp(fields.registry_url) && (
              <label className="insecure-opt-in"><input type="checkbox" checked={fields.allow_insecure_http} onChange={(event) => update("allow_insecure_http", event.target.checked)} /><span><strong>ALLOW HTTP TRANSPORT</strong> Use only on a protected VPN or isolated LAN.</span></label>
            )}
          </div>
          {error && <div className="form-error">{error}</div>}
          <button className="primary-button full-width" disabled={busy || !releases.length}><Plus size={15} /> {busy ? "STARTING …" : "START DEPLOYMENT"}</button>
        </form>
      ) : (
        <div className="modal-body deployment-panel">
          <div className="deployment-ready"><Activity size={22} /><div><strong>{deployment.status.replaceAll("_", " ").toUpperCase()}</strong><span>{deployment.message}</span></div></div>
          <div className="deployment-log" aria-live="polite">{events.map((item) => <div className={"deployment-log-line level-" + item.level} key={item.id}><span>{item.stage}</span>{item.message}</div>)}</div>
          {deployment.status === "awaiting_host_key" && <div className="deployment-confirmation"><strong>VERIFY SSH HOST KEY</strong><code>{deployment.host_key_fingerprint}</code><label className="insecure-opt-in"><input type="checkbox" checked={replaceHostKey} onChange={(event) => setReplaceHostKey(event.target.checked)} /><span>Replace an existing trusted key only when expected.</span></label><button className="primary-button full-width" onClick={confirmHostKey} disabled={busy}><KeyRound size={15} /> TRUST HOST KEY</button></div>}
          {deployment.status === "awaiting_credentials" && <form className="deployment-credentials" onSubmit={submitCredentials}>
            <label className="field-label">SSH PASSWORD<input type="password" autoComplete="new-password" value={credentials.ssh_password} onChange={(event) => setCredentials({ ...credentials, ssh_password: event.target.value })} /></label>
            <label className="field-label">OR PRIVATE KEY<textarea rows="4" value={credentials.ssh_private_key} onChange={(event) => setCredentials({ ...credentials, ssh_private_key: event.target.value })} /></label>
            <label className="field-label">KEY PASSPHRASE<input type="password" autoComplete="off" value={credentials.ssh_key_passphrase} onChange={(event) => setCredentials({ ...credentials, ssh_key_passphrase: event.target.value })} /></label>
            <label className="field-label">SUDO PASSWORD<input type="password" autoComplete="off" value={credentials.sudo_password} onChange={(event) => setCredentials({ ...credentials, sudo_password: event.target.value })} placeholder="Defaults to SSH password" /></label>
            <button className="primary-button full-width" disabled={busy}><KeyRound size={15} /> CONTINUE</button>
          </form>}
          {error && <div className="form-error">{error}</div>}
          <div className="deployment-actions">
            {["pending", "running", "awaiting_host_key", "awaiting_credentials"].includes(deployment.status) && <button className="secondary-button" onClick={cancel} disabled={busy}><X size={15} /> CANCEL</button>}
            {["failed", "cancelled", "interrupted"].includes(deployment.status) && <button className="secondary-button" onClick={retry} disabled={busy}><RotateCcw size={15} /> RETRY</button>}
            {deployment.status === "succeeded" && <button className="primary-button" onClick={() => { onDone(); onClose(); }}><Check size={15} /> DONE</button>}
          </div>
        </div>
      )}
    </Modal>
  );
}
