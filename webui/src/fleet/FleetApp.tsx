// @ts-nocheck
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  Archive,
  Box,
  Ban,
  Check,
  Clock3,
  CloudDownload,
  Database,
  Download,
  HardDrive,
  KeyRound,
  LogOut,
  Plus,
  Radio,
  RefreshCw,
  RotateCcw,
  Server,
  TriangleAlert,
  Upload,
  Wifi,
  WifiOff,
  X,
  Zap,
} from "lucide-react";
import "./styles.css";
import { openDeploymentEvents, request } from "./services/fleetService";
import { wifiNetworkError } from "./wifiValidation.js";
import { deploymentTargetError, hostnameChangeError } from "./deploymentValidation.js";
import { preferredReleaseId } from "./releaseSelection.js";
import {
  ACTION_GROUPS,
  MAINTENANCE_ACTIONS,
  actionAvailability,
  healthTone,
  requiresOverride,
} from "./maintenanceActions.js";

function timeAgo(value) {
  if (!value) return "never";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value));
}

function bytes(value) {
  if (value == null) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let amount = Number(value);
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount.toFixed(index ? 1 : 0)} ${units[index]}`;
}


function formatStopwatch(milliseconds) {
  if (milliseconds == null) return "—";
  const totalHundredths = Math.round(milliseconds / 10);
  const hundredths = totalHundredths % 100;
  const totalSeconds = Math.floor(totalHundredths / 100);
  const seconds = totalSeconds % 60;
  const minutes = Math.floor(totalSeconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(hundredths).padStart(2, "0")}`;
}

function formatDateTime(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("de-DE", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function formatDate(value) {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return value;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return new Intl.DateTimeFormat("de-DE", { dateStyle: "medium" }).format(date);
}

const MIRROR_STATE_LABELS_DE = {
  missing: "kein Spiegel",
  offline: "offline",
  pending: "wird aktualisiert",
  fresh: "aktuell",
};
function mirrorStateLabel(state) {
  return MIRROR_STATE_LABELS_DE[state] || state;
}

const PORTAL_ERROR_MESSAGES_DE = {
  "Device does not exist.": "Dieses Gerät existiert nicht.",
  "Release does not exist.": "Diese Version existiert nicht.",
  "Device access has been revoked.": "Der Gerätezugriff wurde widerrufen.",
  "Device must be online to queue a job.": "Das Gerät muss online sein.",
  "Another disruptive operation is already queued for this device.":
    "Für dieses Gerät ist bereits ein anderer Vorgang eingeplant.",
};
function translatePortalError(message) {
  return PORTAL_ERROR_MESSAGES_DE[message] || message;
}

function insecureRemoteHttp(value) {
  try {
    const parsed = new URL(value);
    const hostname = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
    const loopback = hostname === "localhost" || hostname.endsWith(".localhost")
      || hostname === "::1" || /^127\./.test(hostname);
    return parsed.protocol === "http:" && !loopback;
  } catch {
    return false;
  }
}


function Login({ onLogin }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await request("/api/session", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      await onLogin();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="login-shell">
      <section className="login-card">
        <div className="brand-mark"><Zap size={24} /></div>
        <span className="eyebrow">DEVICE CONTROL PLANE</span>
        <h1>TAKT <em>FLEET</em></h1>
        <p>Manage every timing unit from one secure registry.</p>
        <form onSubmit={submit}>
          <label>
            <span>USERNAME</span>
            <input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="Username" autoComplete="username" />
            <span>PASSWORD</span>
            <div className="password-field">
              <KeyRound size={16} />
              <input
                type="password"
                autoFocus
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="Enter registry password"
              />
            </div>
          </label>
          {error && <div className="form-error">{error}</div>}
          <button className="primary-button full-width" disabled={busy || !password || !username}>
            {busy ? "CONNECTING …" : "OPEN REGISTRY"}
          </button>
        </form>
      </section>
    </main>
  );
}

function Modal({ title, eyebrow, onClose, children, wide = false }) {
  return (
    <div className="modal-layer" role="presentation" onMouseDown={onClose}>
      <section className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <div><span>{eyebrow}</span><h2>{title}</h2></div>
          <button className="icon-button" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </header>
        {children}
      </section>
    </div>
  );
}

function EnrollmentModal({ csrf, onClose, releases, onDone }) {
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

function ReleaseModal({ csrf, onClose, onUploaded }) {
  const [version, setVersion] = useState("");
  const [file, setFile] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const upload = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData();
    data.append("version", version);
    data.append("artifact", file);
    try {
      await request("/api/releases", { method: "POST", body: data }, csrf);
      await onUploaded();
      onClose();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="ADD A TAKT RELEASE" eyebrow="VERSION LIBRARY" onClose={onClose}>
      <form className="modal-body" onSubmit={upload}>
        <p>Upload the Raspberry Pi package created by <code>package_for_raspberry_pi.sh</code>.</p>
        <label className="field-label">VERSION
          <input value={version} onChange={(event) => setVersion(event.target.value)} placeholder="e.g. 0.2.0" />
        </label>
        <label className="file-drop">
          <Upload size={22} />
          <strong>{file ? file.name : "SELECT .TAR.GZ RELEASE"}</strong>
          <small>{file ? bytes(file.size) : "Maximum 250 MB"}</small>
          <input type="file" accept=".gz,.tar.gz" onChange={(event) => setFile(event.target.files[0] || null)} />
        </label>
        {error && <div className="form-error">{error}</div>}
        <button className="primary-button full-width" disabled={busy || !file || !version}>
          {busy ? "UPLOADING …" : "STORE RELEASE"}
        </button>
      </form>
    </Modal>
  );
}

function WifiModal({ device, csrf, onClose, onCreated }) {
  const [ssid, setSsid] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event) => {
    event.preventDefault();
    setError("");
    const validationError = wifiNetworkError(ssid, password);
    if (validationError) {
      setError(validationError);
      return;
    }
    setBusy(true);
    try {
      await request(
        `/api/devices/${device.id}/wifi-networks`,
        { method: "POST", body: JSON.stringify({ ssid, password }) },
        csrf,
      );
      setPassword("");
      await onCreated();
      onClose();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`ADD WI-FI TO ${device.name}`} eyebrow="NETWORK PROFILE" onClose={onClose}>
      <form className="modal-body wifi-fields" onSubmit={submit}>
        <p>
          Save a WPA/WPA2 network without switching the current connection. The profile uses
          the default priority <strong>0</strong>. Send credentials only over HTTPS or a private VPN.
        </p>
        <label className="field-label">SSID
          <input
            autoFocus
            value={ssid}
            onChange={(event) => setSsid(event.target.value)}
            required
          />
        </label>
        <label className="field-label">PASSWORD
          <input
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </label>
        {error && <div className="form-error">{error}</div>}
        <button className="primary-button full-width" disabled={busy || !ssid || !password}>
          <Wifi size={15} /> {busy ? "SAVING …" : "SAVE WI-FI PROFILE"}
        </button>
      </form>
    </Modal>
  );
}

function ConfirmModal({ device, action, onClose, onConfirm }) {
  const definition = MAINTENANCE_ACTIONS[action];
  const needsOverride = requiresOverride(action, device);
  const [override, setOverride] = useState(false);
  const effectiveOverride = needsOverride && override;
  const timerState = device.status?.health?.state || "unknown";
  const blocked = needsOverride && !effectiveOverride;
  return (
    <Modal title={`${definition.label} · ${device.name}`} eyebrow="CONFIRM MAINTENANCE" onClose={onClose}>
      <div className="confirm-body">
        <p>You are about to {definition.confirm} on <strong>{device.name}</strong>.</p>
        {definition.aftermath && <p className="confirm-aftermath">{definition.aftermath}</p>}
        {needsOverride ? (
          <div className="confirm-warning" role="alert">
            <TriangleAlert size={16} />
            <div>
              <strong>THIS PI IS NOT IDLE</strong>
              <span>
                The timer is <strong>{timerState}</strong>. Continuing will interrupt a running or
                unsaved run and that measurement will be lost.
              </span>
              <label className="confirm-override">
                <input
                  type="checkbox"
                  checked={effectiveOverride}
                  onChange={(event) => setOverride(event.target.checked)}
                />
                Interrupt the run anyway
              </label>
            </div>
          </div>
        ) : (
          <p className="confirm-safe">
            The Pi reports timer state <strong>{timerState}</strong>. The agent still re-checks this
            immediately before acting and waits if a run has started in the meantime.
          </p>
        )}
      </div>
      <footer className="modal-actions">
        <button className="secondary-button" onClick={onClose}>CANCEL</button>
        <button
          className={definition.destructive ? "danger-action" : ""}
          disabled={blocked}
          onClick={() => onConfirm(effectiveOverride)}
        >
          {definition.label}
        </button>
      </footer>
    </Modal>
  );
}

function HealthChecks({ healthChecks }) {
  if (!healthChecks?.checks?.length) return null;
  const { summary, checks, collected_at: collectedAt } = healthChecks;
  return (
    <details className="health-panel">
      <summary>
        <span className={`health-dot tone-${healthTone(healthChecks)}`} />
        HEALTH {summary.fail} FAILED · {summary.warn} WARNING · {summary.ok} OK
        <small>{timeAgo(collectedAt)}</small>
      </summary>
      <ul>
        {checks.map((check) => (
          <li key={check.id} className={`tone-${check.status}`}>
            <span>{check.label || check.id}</span>
            <strong>{check.status.toUpperCase()}</strong>
            <small>{check.detail}</small>
          </li>
        ))}
      </ul>
    </details>
  );
}

function MaintenancePanel({ device, diagnostics, onAction }) {
  return (
    <div className="maintenance-panel">
      {ACTION_GROUPS.map((group) => (
        <div className="maintenance-group" key={group.id}>
          <span className="maintenance-label">{group.label}</span>
          <div className="maintenance-buttons">
            {Object.entries(MAINTENANCE_ACTIONS)
              .filter(([, definition]) => definition.group === group.id)
              .map(([action, definition]) => {
                const { enabled, reason } = actionAvailability(action, device);
                return (
                  <button
                    key={action}
                    className={definition.destructive ? "danger-action" : ""}
                    disabled={!enabled}
                    title={reason}
                    onClick={() => onAction(device, action)}
                  >
                    {definition.label}
                  </button>
                );
              })}
          </div>
        </div>
      ))}
      {diagnostics?.length > 0 && (
        <div className="maintenance-group">
          <span className="maintenance-label">BUNDLES</span>
          <div className="maintenance-bundles">
            {diagnostics.map((bundle) => (
              <a
                key={bundle.id}
                href={`/api/devices/${device.id}/diagnostics/${bundle.id}`}
                title={`${bytes(bundle.size)} · redacted diagnostics`}
              >
                <Download size={13} /> {timeAgo(bundle.created_at)}
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function DeviceCard({ device, releases, job, diagnostics, onJob, onCancel, onRetry, onRevoke, onWifi, onMaintenance }) {
  const [releaseId, setReleaseId] = useState(preferredReleaseId(releases));
  const effectiveReleaseId = releaseId || preferredReleaseId(releases);
  const status = device.status || {};
  const health = status.health || {};
  const updateRecovery = status.update_recovery?.stuck ? status.update_recovery : null;
  const diskFree = status.disk_free_bytes;
  const protocolVersion = status.protocol_version;
  const protocolOk = protocolVersion === 1;
  const neverSeen = Object.keys(status).length === 0;
  const protocolLegacy = !neverSeen && !protocolOk;
  const wifiCapable = status.capabilities?.includes("wifi-profile-v1");
  const connectionParts = [
    status.registry_rtt_ms != null ? `${status.registry_rtt_ms} ms` : null,
    status.wifi_signal_dbm != null ? `${status.wifi_signal_dbm} dBm` : null,
    protocolVersion != null ? `protocol ${protocolVersion}` : "waiting for heartbeat",
    status.registry_transport === "insecure-http-opt-in" ? "HTTP opt-in" : status.registry_transport,
  ].filter(Boolean);
  const installActive = job && ["queued", "claimed", "running"].includes(job.status);
  const installRetryable = job && ["rolled_back", "failed", "cancelled"].includes(job.status);
  const canCancel = installActive && !["activating", "restarting", "health_checking"].includes(job.stage);
  const stageLabel = job?.stage?.replaceAll("_", " ") || job?.status;
  const transfer = job?.bytes_total != null
    ? `${bytes(job.bytes_downloaded || 0)} / ${bytes(job.bytes_total)}` : null;
  return (
    <article className={`device-card ${device.online ? "is-online" : "is-offline"} ${updateRecovery ? "has-recovery" : ""} ${device.revoked_at ? "is-revoked" : ""}`}>
      <header>
        <div className="device-icon"><Server size={19} /></div>
        <div className="device-title">
          <div><span className="status-dot" />{device.revoked_at ? "REVOKED" : updateRecovery ? "FLEET RECOVERY NEEDED" : device.online ? "ONLINE" : "OFFLINE"}</div>
          <h3>{device.name}</h3>
          <small>{device.hostname}</small>
        </div>
        <div className="version-badge"><span>TAKT</span><strong>{device.app_version || "—"}</strong></div>
      </header>
      <div className="device-metrics">
        <div><Activity size={14} /><span>Timer</span><strong>{health.state || "unknown"}</strong></div>
        <div><HardDrive size={14} /><span>Free</span><strong>{bytes(diskFree)}</strong></div>
        <div><Database size={14} /><span>Runs</span><strong>{device.run_count ?? "—"}</strong></div>
        <div><Clock3 size={14} /><span>Seen</span><strong>{timeAgo(device.last_seen_at)}</strong></div>
      </div>
      <div className={`connection-row ${protocolOk || !protocolLegacy ? "" : "connection-warning"}`}>
        <Radio size={15} />
        <span>AGENT LINK<small>{connectionParts.join(" · ")}</small></span>
        <strong>{protocolOk ? "COMPATIBLE" : protocolLegacy ? "FLEET AGENT UPDATE REQUIRED" : "WAITING FOR HEARTBEAT"}</strong>
      </div>
      {updateRecovery && (
        <div className="recovery-row" role="alert">
          <TriangleAlert size={16} />
          <span>
            UPDATE RECOVERY NEEDS FLEET ATTENTION
            <small>{updateRecovery.phase || "unknown"} · {updateRecovery.error || "Use the available Fleet retry control."}</small>
          </span>
          <strong>FLEET RECOVERY</strong>
        </div>
      )}
      <div className="mirror-row">
        <div>
          <Database size={15} />
          <span>DATA MIRROR<small>{device.last_mirror_at ? `${timeAgo(device.last_mirror_at)} · ${bytes(device.mirror_size)}` : "Not mirrored yet"}</small></span>
        </div>
        {device.last_mirror_at && (
          <a href={`/api/devices/${device.id}/mirror`} title="Download mirrored database"><Download size={16} /></a>
        )}
      </div>
      {job && (
        <div className="recovery-row" role="status">
          <Activity size={16} />
          <span>
            INSTALL {job.current_version || device.app_version || "—"} → {job.target_version || "—"}
            <small>{stageLabel} · {job.message || "Waiting for agent"}{transfer ? ` · ${transfer}` : ""} · updated {timeAgo(job.updated_at)}</small>
          </span>
          <div>
            {canCancel && <button className="secondary-button" onClick={() => onCancel(job)}>CANCEL</button>}
            {installRetryable && <button className="secondary-button" onClick={() => onRetry(job)}><RotateCcw size={14} /> RETRY</button>}
          </div>
        </div>
      )}
      <div className="update-control">
        <label>INSTALL VERSION
          <select value={effectiveReleaseId} onChange={(event) => setReleaseId(event.target.value)}>
            {!releases.length && <option value="">No releases uploaded</option>}
            {releases.map((release) => <option value={release.id} key={release.id}>{release.version}{release.source === "bundled" ? " · VERIFIED" : ""}</option>)}
          </select>
        </label>
        <button
          disabled={!device.online || protocolLegacy || !effectiveReleaseId || updateRecovery || device.revoked_at || installActive}
          title={protocolLegacy ? "This Pi needs a compatible Fleet agent before remote installs" : ""}
          onClick={() => onJob(device, "install_release", { release_id: effectiveReleaseId })}
        ><CloudDownload size={16} /> INSTALL</button>
      </div>
      <HealthChecks healthChecks={device.health_checks} />
      <MaintenancePanel device={device} diagnostics={diagnostics} onAction={onMaintenance} />
      <footer>
        <button disabled={!device.online || updateRecovery || device.revoked_at} onClick={() => onJob(device, "mirror_now")}><Database size={14} /> MIRROR NOW</button>
        <button
          disabled={!device.online || !wifiCapable || updateRecovery || device.revoked_at}
          title={!wifiCapable ? "Rerun the Pi installer once to enable Fleet Wi-Fi" : ""}
          onClick={() => onWifi(device)}
        ><Wifi size={14} /> ADD WI-FI</button>
        <button className="danger-action" disabled={device.revoked_at} onClick={() => onRevoke(device)}><Ban size={14} /> REVOKE</button>
      </footer>
    </article>
  );
}

function JobRow({ job, onCancel, onRetry }) {
  const active = ["queued", "claimed", "running"].includes(job.status);
  const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
  return (
    <div className="job-row">
      <div className={`job-icon status-${job.status}`}>{active ? <RefreshCw size={15} /> : job.status === "succeeded" ? <Check size={15} /> : <X size={15} />}</div>
      <div className="job-copy">
        <strong>{job.action.replaceAll("_", " ")}</strong>
        <span>{job.device_name} · {job.stage?.replaceAll("_", " ") || job.status} · {job.message || job.status}{job.bytes_total != null ? ` · ${bytes(job.bytes_downloaded || 0)} / ${bytes(job.bytes_total)}` : ""}{job.attempt > 1 ? ` · attempt ${job.attempt}` : ""}</span>
      </div>
      <progress
        className="job-progress"
        max="100"
        value={progress}
        aria-label={`${job.action.replaceAll("_", " ")} progress`}
      />
      {job.action === "install_release" && active && !["activating", "restarting", "health_checking"].includes(job.stage) && <button className="secondary-button" onClick={() => onCancel(job)}>CANCEL</button>}
      {job.action !== "add_wifi_network" && ["rolled_back", "failed", "cancelled"].includes(job.status) && <button className="secondary-button" onClick={() => onRetry(job)}><RotateCcw size={14} /> RETRY</button>}
      <time>{timeAgo(job.updated_at)}</time>
    </div>
  );
}

function Dashboard({ session, refreshSession }) {
  const [devices, setDevices] = useState([]);
  const [releases, setReleases] = useState([]);
  const [bundledRelease, setBundledRelease] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [modal, setModal] = useState(null);
  const diagnosticsSignature = useRef(null);
  const [wifiDevice, setWifiDevice] = useState(null);
  const [confirmation, setConfirmation] = useState(null);
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
      setError(failure.message);
    }
  }, []);
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
  // Maintenance actions always go through the confirmation dialog, which is
  // also where an override for a busy timer is granted.
  const confirmMaintenance = async (override) => {
    const pending = confirmation;
    setConfirmation(null);
    if (pending) await submitJob(pending.device, pending.action, {}, override);
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
  return (
    <div className="fleet-app">
      <header className="topbar">
        <div className="brand"><div className="brand-mark"><Zap size={18} /></div><strong>TAKT <em>FLEET</em></strong></div>
        <div className="registry-state"><Radio size={14} /><span>REGISTRY ACTIVE</span></div>
        <div className="top-actions">
          <button onClick={() => setModal("enroll")}><Plus size={15} /> ENROLL DEVICE</button>
          <button onClick={() => setModal("release")}><Upload size={15} /> ADD RELEASE</button>
          <button className="icon-button" onClick={logout} title="Log out"><LogOut size={17} /></button>
        </div>
      </header>
      <main>
        <section className="hero">
          <div><span className="eyebrow">CENTRAL OPERATIONS</span><h1>DEVICE REGISTRY</h1><p>Version control, health and mirrored timing data across every Raspberry Pi.</p></div>
          <div className="summary-grid">
            <div><Wifi size={18} /><strong>{online}<small>/{devices.length}</small></strong><span>ONLINE</span></div>
            <div><Box size={18} /><strong>{releases.length}</strong><span>RELEASES</span></div>
            <div><Archive size={18} /><strong>{mirroredRuns}</strong><span>MIRRORED RUNS</span></div>
          </div>
        </section>
        {insecureLan && <div className="security-warning"><WifiOff size={16} /><span><strong>UNENCRYPTED REGISTRY</strong> Use HTTPS or a private Tailscale/WireGuard network before installing releases or operating outside an isolated LAN.</span></div>}
        {bundledRelease?.status === "error" && <div className="security-warning is-danger"><TriangleAlert size={16} /><span><strong>BUNDLED RELEASE UNAVAILABLE</strong> {bundledRelease.detail || `Reason: ${bundledRelease.reason}`}. Upload a release manually until this image is rebuilt.</span></div>}
        {error && <div className="global-error"><WifiOff size={16} />{error}</div>}
        <section className="section-heading"><div><span>01 · APPLIANCES</span><h2>RASPBERRY PI FLEET</h2></div><button onClick={load}><RefreshCw size={14} /> REFRESH</button></section>
        <section className="device-grid">
          {devices.map((device) => <DeviceCard key={device.id} device={device} releases={releases} job={jobs.find((job) => job.device_id === device.id && job.action === "install_release")} diagnostics={diagnostics[device.id]} onJob={createJob} onCancel={cancelJob} onRetry={retryJob} onRevoke={revokeDevice} onWifi={setWifiDevice} onMaintenance={(target, action) => setConfirmation({ device: target, action })} />)}
          {!devices.length && <div className="empty-card"><Server size={28} /><h3>NO DEVICES ENROLLED</h3><p>Start a guided deployment to connect the first Raspberry Pi.</p><button className="primary-button" onClick={() => setModal("enroll")}>ENROLL FIRST DEVICE</button></div>}
        </section>
        <section className="operations">
          <div className="section-heading"><div><span>02 · ACTIVITY</span><h2>DEPLOYMENT JOBS</h2></div></div>
          <div className="job-list">
            {jobs.slice(0, 12).map((job) => <JobRow key={job.id} job={job} onCancel={cancelJob} onRetry={retryJob} />)}
            {!jobs.length && <div className="jobs-empty">No remote operations have been requested.</div>}
          </div>
        </section>
        <UserAdminPanel csrf={session.csrf_token} devices={devices} />
      </main>
      {modal === "enroll" && <EnrollmentModal csrf={session.csrf_token} releases={releases} onDone={load} onClose={() => setModal(null)} />}
      {modal === "release" && <ReleaseModal csrf={session.csrf_token} onClose={() => setModal(null)} onUploaded={load} />}
      {wifiDevice && <WifiModal device={wifiDevice} csrf={session.csrf_token} onClose={() => setWifiDevice(null)} onCreated={load} />}
      {confirmation && (
        <ConfirmModal
          device={devices.find((item) => item.id === confirmation.device.id) || confirmation.device}
          action={confirmation.action}
          onClose={() => setConfirmation(null)}
          onConfirm={confirmMaintenance}
        />
      )}
    </div>
  );
}

function App() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const refreshSession = useCallback(async () => {
    const result = await request("/api/session");
    setSession(result.authenticated ? result : null);
    setLoading(false);
  }, []);
  useEffect(() => { queueMicrotask(refreshSession); }, [refreshSession]);
  if (loading) return <div className="boot-screen"><Zap size={22} /> TAKT FLEET</div>;

  if (session?.user?.must_change_password) return <PasswordChange session={session} refreshSession={refreshSession} />;
  return session ? ((session.user?.is_admin ?? true) ? <Dashboard session={session} refreshSession={refreshSession} /> : <Portal session={session} refreshSession={refreshSession} />) : <Login onLogin={refreshSession} />;
}
export default App;
function UserAdminPanel({ csrf, devices }) { const [users, setUsers] = useState([]); const [username, setUsername] = useState(""); const [temporaryPassword, setTemporaryPassword] = useState(""); const [error, setError] = useState(""); const load = useCallback(async () => { try { setUsers((await request("/api/admin/users")).users || []); } catch (failure) { setError(failure.message); } }, []); useEffect(() => { queueMicrotask(load); }, [load]); const create = async (event) => { event.preventDefault(); try { const result = await request("/api/admin/users", { method: "POST", body: JSON.stringify({ username }) }, csrf); setTemporaryPassword(result.temporary_password); setUsername(""); await load(); } catch (failure) { setError(failure.message); } }; const changeState = async (user) => { try { await request("/api/admin/users/" + user.id, { method: "PATCH", body: JSON.stringify({ disabled: !user.disabled }) }, csrf); await load(); } catch (failure) { setError(failure.message); } }; const reset = async (user) => { try { const result = await request("/api/admin/users/" + user.id + "/reset-password", { method: "POST", body: JSON.stringify({}) }, csrf); setTemporaryPassword(result.temporary_password); } catch (failure) { setError(failure.message); } }; const grant = async (user, deviceId, access) => { try { await request("/api/admin/users/" + user.id + "/devices/" + deviceId, { method: "PUT", body: JSON.stringify({ access }) }, csrf); await load(); } catch (failure) { setError(failure.message); } }; return <section className="operations"><div className="section-heading"><div><span>03 · ACCESS</span><h2>USERS AND DEVICE ACCESS</h2></div><button onClick={load}><RefreshCw size={14} /> REFRESH</button></div><form className="enrollment-fields" onSubmit={create}><label className="field-label">USERNAME<input value={username} onChange={(event) => setUsername(event.target.value)} /></label><button className="primary-button" disabled={!username}>CREATE USER</button></form>{temporaryPassword && <div className="security-warning"><strong>ONE-TIME PASSWORD:</strong> <code>{temporaryPassword}</code></div>}{error && <div className="form-error">{error}</div>}<div className="job-list">{users.map((user) => <div className="job-row" key={user.id}><div className="job-copy"><strong>{user.username}{user.is_admin ? " · ADMIN" : ""}</strong><span>{user.disabled ? "DISABLED" : "ACTIVE"}</span></div><button className="secondary-button" onClick={() => reset(user)}>RESET PASSWORD</button><button className="secondary-button" onClick={() => changeState(user)}>{user.disabled ? "ENABLE" : "DISABLE"}</button>{devices.map((device) => <select key={device.id} value={(user.access || []).find((item) => item.device_id === device.id)?.access_level || "none"} onChange={(event) => event.target.value !== "none" && grant(user, device.id, event.target.value)}><option value="none">{device.name}: none</option><option value="read">{device.name}: read</option><option value="write">{device.name}: write</option></select>)}</div>)}</div></section>; }
function Portal({ session, refreshSession }) {
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
      setError(translatePortalError(failure.message));
    }
  }, []);
  const loadRuns = useCallback(async () => {
    if (!deviceId) return;
    try {
      const query = new URLSearchParams();
      if (from) query.set("from", from);
      if (to) query.set("to", to);
      setRuns(await request("/api/portal/devices/" + deviceId + "/runs?" + query));
      setError("");
    } catch (failure) {
      setError(translatePortalError(failure.message));
    }
  }, [deviceId, from, to]);
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
  return (
    <div className="fleet-app portal-app">
      <header className="topbar">
        <div className="brand"><div className="brand-mark"><Zap size={18} /></div><strong>TAKT <em>LÄUFE</em></strong></div>
        <div className="top-actions">
          <span className="portal-username">{session.user?.username}</span>
          <button className="icon-button" onClick={logout} title="Abmelden"><LogOut size={17} /></button>
        </div>
      </header>
      <main>
        <section className="hero">
          <div>
            <span className="eyebrow">AUTORISIERTES LAUFPORTAL</span>
            <h1>GESPIEGELTE LÄUFE</h1>
            <p>Schreibgeschützte Momentaufnahmen bleiben in der Registry; Änderungen werden an den maßgeblichen Pi gesendet.</p>
          </div>
        </section>
        {error && <div className="global-error">{error}</div>}
        <section className="section-heading">
          <div><span>GERÄTE</span><h2>IHRE TAKT-GERÄTE</h2></div>
          <button onClick={loadDevices}><RefreshCw size={14} /> AKTUALISIEREN</button>
        </section>
        <section className="device-grid">
          {devices.map((device) => (
            <button className={"device-card portal-device " + (device.id === deviceId ? "selected" : "")} key={device.id} onClick={() => setDeviceId(device.id)}>
              <strong>{device.name}</strong>
              <span>{mirrorStateLabel(device.mirror_state)} · {device.run_count ?? 0} Läufe</span>
              <small>{formatDateTime(device.last_mirrored_at) || "Noch kein Spiegel"}</small>
            </button>
          ))}
          {!devices.length && (
            <div className="empty-card">
              <h3>KEINE ZUGEWIESENEN GERÄTE</h3>
              <p>Bitte einen Administrator um Gerätezugriff.</p>
            </div>
          )}
        </section>
        {runs && (
          <section className="operations">
            <div className="section-heading">
              <div><span>LAUFVERLAUF</span><h2>{runs.summary.count} LÄUFE</h2></div>
              <span>{mirrorStateLabel(runs.mirror.state)} · {formatDateTime(runs.mirror.last_mirrored_at) || "noch nicht gespiegelt"}</span>
            </div>
            <div className="enrollment-fields">
              <label className="field-label">VON<input type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
              <label className="field-label">BIS<input type="date" value={to} onChange={(event) => setTo(event.target.value)} /></label>
            </div>
            <div className="summary-grid">
              <div><strong>{formatStopwatch(runs.summary.best_total_ms)}</strong><span>BESTZEIT</span></div>
              <div><strong>{formatStopwatch(Math.round(runs.summary.average_total_ms || 0))}</strong><span>DURCHSCHNITT</span></div>
              <div><strong>{formatStopwatch(runs.summary.added_time_ms)}</strong><span>ZUSCHLAG</span></div>
            </div>
            <div className="job-list">
              {runs.runs.map((run) => (
                <article className="job-row" key={run.id}>
                  <div className="job-copy">
                    <strong>Lauf {run.run_number} · {formatDate(run.session_date)}</strong>
                    <span>{formatStopwatch(run.total_time_ms)} gesamt · {formatStopwatch(run.actual_time_ms)} Ist-Zeit · +{formatStopwatch(run.added_time_ms)} Zuschlag</span>
                  </div>
                  {(devices.find((item) => item.id === deviceId)?.access === "write" || session.user?.is_admin) && (
                    <>
                      <button className="secondary-button" onClick={() => command(run, "adjust_added_time", Math.max(0, run.added_time_ms + 5000))}>+5 s</button>
                      <button className="secondary-button" onClick={() => command(run, "delete")}>LÖSCHEN</button>
                    </>
                  )}
                </article>
              ))}
              {!runs.runs.length && <div className="jobs-empty">Keine Läufe im gewählten Zeitraum.</div>}
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
function PasswordChange({ session, refreshSession }) { const [currentPassword, setCurrentPassword] = useState(""); const [newPassword, setNewPassword] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false); const submit = async (event) => { event.preventDefault(); setBusy(true); setError(""); try { await request("/api/session/password", { method: "POST", body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) }, session.csrf_token); await refreshSession(); } catch (failure) { setError(failure.message); } finally { setBusy(false); } }; return <div className="fleet-app"><main><section className="hero"><div><span className="eyebrow">SECURITY</span><h1>CHANGE PASSWORD</h1><p>Your temporary password must be replaced before continuing.</p></div></section><form className="enrollment-fields" onSubmit={submit}><label className="field-label">CURRENT PASSWORD<input type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} autoComplete="current-password" /></label><label className="field-label">NEW PASSWORD<input type="password" minLength={12} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} autoComplete="new-password" /></label>{error && <div className="form-error">{error}</div>}<button className="primary-button" disabled={busy || !currentPassword || newPassword.length < 12}>{busy ? "SAVING …" : "SET PASSWORD"}</button></form></main></div>; }
