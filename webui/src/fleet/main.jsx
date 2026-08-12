/* eslint-disable react-refresh/only-export-components */
import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  Archive,
  Box,
  Ban,
  Check,
  Clock3,
  CloudDownload,
  Copy,
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
  Terminal,
  Upload,
  Wifi,
  WifiOff,
  X,
  Zap,
} from "lucide-react";
import "./styles.css";
import { wifiNetworkError } from "./wifiValidation.js";

async function request(url, options = {}, csrf = "") {
  const headers = { ...options.headers };
  if (csrf) headers["X-CSRF-Token"] = csrf;
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(url, { ...options, headers });
  if (!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`);
  return response.json();
}

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

function shellQuote(value) {
  return `'${String(value).replaceAll("'", `'"'"'`)}'`;
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

async function copyText(value) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      // LAN deployments commonly use HTTP, where the Clipboard API can be blocked.
    }
  }
  const input = document.createElement("textarea");
  input.value = value;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.append(input);
  input.select();
  const copied = document.execCommand("copy");
  input.remove();
  if (!copied) throw new Error("Copying is unavailable. Select the command manually.");
}

function Login({ onLogin }) {
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
        body: JSON.stringify({ password }),
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
            <span>ADMIN PASSWORD</span>
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
          <button className="primary-button" disabled={busy || !password}>
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

function EnrollmentModal({ csrf, onClose }) {
  const [deviceName, setDeviceName] = useState("Bahn 1");
  const [hostname, setHostname] = useState("takt-01");
  const [sshUser, setSshUser] = useState("");
  const [piAddress, setPiAddress] = useState("raspberrypi.local");
  const [registryUrl, setRegistryUrl] = useState(() => window.location.origin);
  const [allowInsecureHttp, setAllowInsecureHttp] = useState(false);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copyLabel, setCopyLabel] = useState("COPY DEPLOY COMMAND");

  const validate = () => {
    if (!deviceName.trim() || !/^[A-Za-z0-9ÄÖÜäöüß._ -]+$/.test(deviceName)) {
      return "Device name may contain letters, numbers, spaces, dots, underscores and hyphens.";
    }
    if (!/^[A-Za-z0-9][A-Za-z0-9-]{0,62}$/.test(hostname)) {
      return "Hostname must start with a letter or number and contain only letters, numbers and hyphens.";
    }
    if (!/^[A-Za-z_][A-Za-z0-9_-]{0,31}$/.test(sshUser)) {
      return "SSH user is invalid.";
    }
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,253}$/.test(piAddress)) {
      return "Pi address must be a hostname or IPv4 address without spaces.";
    }
    try {
      const parsed = new URL(registryUrl);
      if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) {
        return "Registry URL must be an HTTP(S) address without embedded credentials.";
      }
    } catch {
      return "Registry URL is invalid.";
    }
    if (insecureRemoteHttp(registryUrl) && !allowInsecureHttp) {
      return "Use HTTPS, or explicitly acknowledge HTTP over your private VPN/isolated LAN.";
    }
    return "";
  };

  const create = async (event) => {
    event.preventDefault();
    setError("");
    const validationError = validate();
    if (validationError) {
      setError(validationError);
      return;
    }
    setBusy(true);
    try {
      const result = await request(
        "/api/enrollment-codes",
        { method: "POST", body: JSON.stringify({ label: deviceName.trim() }) },
        csrf,
      );
      setCode(result.code);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };

  const normalizedRegistryUrl = (() => {
    try {
      return new URL(registryUrl).toString().replace(/\/$/, "");
    } catch {
      return registryUrl.trim().replace(/\/$/, "");
    }
  })();
  const deployCommand = code ? [
    `TAKT_REGISTRY_URL=${shellQuote(normalizedRegistryUrl)} \\`,
    ...(insecureRemoteHttp(normalizedRegistryUrl)
      ? [`TAKT_REGISTRY_ALLOW_INSECURE_HTTP='true' \\`]
      : []),
    `TAKT_ENROLLMENT_CODE=${shellQuote(code)} \\`,
    `TAKT_DEVICE_NAME=${shellQuote(deviceName.trim())} \\`,
    `TAKT_HOSTNAME=${shellQuote(hostname)} \\`,
    `  ./scripts/deploy_to_raspberry_pi.sh ${shellQuote(`${sshUser}@${piAddress}`)}`,
  ].join("\n") : "";

  const copyCommand = async () => {
    try {
      await copyText(deployCommand);
      setCopyLabel("COPIED");
      window.setTimeout(() => setCopyLabel("COPY DEPLOY COMMAND"), 1800);
    } catch (failure) {
      setError(failure.message);
    }
  };

  return (
    <Modal title="ENROLL A RASPBERRY PI" eyebrow="GUIDED FIRST DEPLOYMENT" onClose={onClose} wide>
      {!code ? (
        <form className="modal-body" onSubmit={create}>
          <p>Enter the individual parts once. The registry will create a safe command for the one-time SSH installation.</p>
          <div className="enrollment-fields">
            <label className="field-label">DEVICE NAME
              <input value={deviceName} onChange={(event) => setDeviceName(event.target.value)} placeholder="e.g. Bahn 1" required />
            </label>
            <label className="field-label">TAKT HOSTNAME
              <input value={hostname} onChange={(event) => setHostname(event.target.value)} placeholder="e.g. takt-01" required />
            </label>
            <label className="field-label">RASPBERRY PI SSH USER
              <input value={sshUser} onChange={(event) => setSshUser(event.target.value)} placeholder="e.g. pi" required />
            </label>
            <label className="field-label">RASPBERRY PI ADDRESS
              <input value={piAddress} onChange={(event) => setPiAddress(event.target.value)} placeholder="e.g. raspberrypi.local" required />
            </label>
            <label className="field-label enrollment-url">REGISTRY URL REACHABLE BY THE PI
              <input value={registryUrl} onChange={(event) => setRegistryUrl(event.target.value)} placeholder="e.g. http://192.168.1.10:8090" required />
            </label>
            {insecureRemoteHttp(registryUrl) && (
              <label className="insecure-opt-in">
                <input
                  type="checkbox"
                  checked={allowInsecureHttp}
                  onChange={(event) => setAllowInsecureHttp(event.target.checked)}
                />
                <span>
                  <strong>ALLOW HTTP TRANSPORT</strong>
                  Without a private VPN, HTTP exposes device data and cannot authenticate remote releases. Use only on a protected VPN or consciously isolated LAN.
                </span>
              </label>
            )}
          </div>
          {error && <div className="form-error">{error}</div>}
          <button className="primary-button" disabled={busy}>
            <Plus size={15} /> {busy ? "CREATING …" : "CREATE DEPLOY COMMAND"}
          </button>
        </form>
      ) : (
        <div className="modal-body deployment-panel">
          <div className="deployment-ready"><Check size={22} /><div><strong>DEPLOYMENT READY</strong><span>Enrollment code expires in 60 minutes</span></div></div>
          <p>
            Run this once from the TAKT repository on your laptop. It connects to
            <strong> {sshUser}@{piAddress}</strong> and enrolls the agent with this registry.
          </p>
          <pre className="deploy-command"><code>{deployCommand}</code></pre>
          {error && <div className="form-error">{error}</div>}
          <button className="secondary-button" onClick={copyCommand}><Copy size={15} /> {copyLabel}</button>
          <div className="deployment-note"><Terminal size={15} /><span>SSH is needed only for this first installation. Future versions are installed from the registry.</span></div>
          {insecureRemoteHttp(normalizedRegistryUrl) && (
            <div className="deployment-note insecure-note"><WifiOff size={15} /><span>This device explicitly permits HTTP. Treat remote installs as trusted only when this path is protected by a private VPN or isolated LAN.</span></div>
          )}
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
        <button className="primary-button" disabled={busy || !file || !version}>
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
        <button className="primary-button" disabled={busy || !ssid || !password}>
          <Wifi size={15} /> {busy ? "SAVING …" : "SAVE WI-FI PROFILE"}
        </button>
      </form>
    </Modal>
  );
}

function DeviceCard({ device, releases, onJob, onRevoke, onWifi }) {
  const [releaseId, setReleaseId] = useState(releases[0]?.id || "");
  const effectiveReleaseId = releaseId || releases[0]?.id || "";
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
  return (
    <article className={`device-card ${device.online ? "is-online" : "is-offline"} ${updateRecovery ? "has-recovery" : ""} ${device.revoked_at ? "is-revoked" : ""}`}>
      <header>
        <div className="device-icon"><Server size={19} /></div>
        <div className="device-title">
          <div><span className="status-dot" />{device.revoked_at ? "REVOKED" : updateRecovery ? "REPAIR REQUIRED" : device.online ? "ONLINE" : "OFFLINE"}</div>
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
        <strong>{protocolOk ? "COMPATIBLE" : protocolLegacy ? "UPDATE VIA SSH" : "WAITING FOR HEARTBEAT"}</strong>
      </div>
      {updateRecovery && (
        <div className="recovery-row" role="alert">
          <TriangleAlert size={16} />
          <span>
            UPDATE RECOVERY BLOCKED
            <small>{updateRecovery.phase || "unknown"} · {updateRecovery.error || "Manual repair is required."}</small>
          </span>
          <strong>MANUAL REPAIR</strong>
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
      <div className="update-control">
        <label>INSTALL VERSION
          <select value={effectiveReleaseId} onChange={(event) => setReleaseId(event.target.value)}>
            {!releases.length && <option value="">No releases uploaded</option>}
            {releases.map((release) => <option value={release.id} key={release.id}>{release.version}</option>)}
          </select>
        </label>
        <button
          disabled={!device.online || protocolLegacy || !effectiveReleaseId || updateRecovery || device.revoked_at}
          title={protocolLegacy ? "Update the Pi agent once via SSH before remote installs" : ""}
          onClick={() => onJob(device, "install_release", { release_id: effectiveReleaseId })}
        ><CloudDownload size={16} /> INSTALL</button>
      </div>
      <footer>
        <button disabled={!device.online || updateRecovery || device.revoked_at} onClick={() => onJob(device, "mirror_now")}><Database size={14} /> MIRROR NOW</button>
        <button disabled={!device.online || protocolLegacy || updateRecovery || device.revoked_at} onClick={() => onJob(device, "restart_takt")}><RotateCcw size={14} /> RESTART</button>
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

function JobRow({ job }) {
  const active = ["queued", "claimed", "running"].includes(job.status);
  const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
  return (
    <div className="job-row">
      <div className={`job-icon status-${job.status}`}>{active ? <RefreshCw size={15} /> : job.status === "succeeded" ? <Check size={15} /> : <X size={15} />}</div>
      <div className="job-copy">
        <strong>{job.action.replaceAll("_", " ")}</strong>
        <span>{job.device_name} · {job.message || job.status}{job.attempt > 1 ? ` · attempt ${job.attempt}` : ""}</span>
      </div>
      <progress
        className="job-progress"
        max="100"
        value={progress}
        aria-label={`${job.action.replaceAll("_", " ")} progress`}
      />
      <time>{timeAgo(job.updated_at)}</time>
    </div>
  );
}

function Dashboard({ session, refreshSession }) {
  const [devices, setDevices] = useState([]);
  const [releases, setReleases] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [modal, setModal] = useState(null);
  const [wifiDevice, setWifiDevice] = useState(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const [deviceData, releaseData, jobData] = await Promise.all([
        request("/api/devices"), request("/api/releases"), request("/api/jobs"),
      ]);
      setDevices(deviceData.devices);
      setReleases(releaseData.releases);
      setJobs(jobData.jobs);
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
  const createJob = async (device, action, payload = {}) => {
    const labels = {
      install_release: "install the selected version",
      mirror_now: "mirror its database now",
      restart_takt: "restart TAKT",
    };
    if (!window.confirm(`${device.name}: ${labels[action]}?`)) return;
    try {
      await request(
        `/api/devices/${device.id}/jobs`,
        { method: "POST", body: JSON.stringify({ action, payload }) },
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
        {error && <div className="global-error"><WifiOff size={16} />{error}</div>}
        <section className="section-heading"><div><span>01 · APPLIANCES</span><h2>RASPBERRY PI FLEET</h2></div><button onClick={load}><RefreshCw size={14} /> REFRESH</button></section>
        <section className="device-grid">
          {devices.map((device) => <DeviceCard key={device.id} device={device} releases={releases} onJob={createJob} onRevoke={revokeDevice} onWifi={setWifiDevice} />)}
          {!devices.length && <div className="empty-card"><Server size={28} /><h3>NO DEVICES ENROLLED</h3><p>Create an enrollment code to connect the first Raspberry Pi.</p><button className="primary-button" onClick={() => setModal("enroll")}>ENROLL FIRST DEVICE</button></div>}
        </section>
        <section className="operations">
          <div className="section-heading"><div><span>02 · ACTIVITY</span><h2>DEPLOYMENT JOBS</h2></div></div>
          <div className="job-list">
            {jobs.slice(0, 12).map((job) => <JobRow key={job.id} job={job} />)}
            {!jobs.length && <div className="jobs-empty">No remote operations have been requested.</div>}
          </div>
        </section>
      </main>
      {modal === "enroll" && <EnrollmentModal csrf={session.csrf_token} onClose={() => setModal(null)} />}
      {modal === "release" && <ReleaseModal csrf={session.csrf_token} onClose={() => setModal(null)} onUploaded={load} />}
      {wifiDevice && <WifiModal device={wifiDevice} csrf={session.csrf_token} onClose={() => setWifiDevice(null)} onCreated={load} />}
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
  return session ? <Dashboard session={session} refreshSession={refreshSession} /> : <Login onLogin={refreshSession} />;
}

createRoot(document.getElementById("root")).render(<StrictMode><App /></StrictMode>);
