/* eslint-disable react-refresh/only-export-components */
import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  Archive,
  Box,
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
  Upload,
  Wifi,
  WifiOff,
  X,
  Zap,
} from "lucide-react";
import "./styles.css";

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

function Modal({ title, eyebrow, onClose, children }) {
  return (
    <div className="modal-layer" role="presentation" onMouseDown={onClose}>
      <section className="modal" role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()}>
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
  const [label, setLabel] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const create = async () => {
    setError("");
    try {
      const result = await request(
        "/api/enrollment-codes",
        { method: "POST", body: JSON.stringify({ label }) },
        csrf,
      );
      setCode(result.code);
    } catch (failure) {
      setError(failure.message);
    }
  };
  return (
    <Modal title="ENROLL A RASPBERRY PI" eyebrow="ONE-TIME CONNECTION" onClose={onClose}>
      {!code ? (
        <div className="modal-body">
          <p>Create a one-time code, then use it while installing the Pi agent. It expires after 15 minutes.</p>
          <label className="field-label">DEVICE LABEL (OPTIONAL)
            <input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="e.g. Training lane 1" />
          </label>
          {error && <div className="form-error">{error}</div>}
          <button className="primary-button" onClick={create}><Plus size={15} /> CREATE CODE</button>
        </div>
      ) : (
        <div className="modal-body code-panel">
          <Check size={26} />
          <p>Use this code once. Treat it like a temporary password.</p>
          <code>{code}</code>
          <p className="enrollment-hint">
            Pass it as <strong>TAKT_ENROLLMENT_CODE</strong> together with a registry URL
            that the Pi can reach over Wi-Fi.
          </p>
          <button className="secondary-button" onClick={() => navigator.clipboard.writeText(code)}>COPY CODE</button>
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

function DeviceCard({ device, releases, onJob }) {
  const [releaseId, setReleaseId] = useState(releases[0]?.id || "");
  const effectiveReleaseId = releaseId || releases[0]?.id || "";
  const status = device.status || {};
  const health = status.health || {};
  const diskFree = status.disk_free_bytes;
  return (
    <article className={`device-card ${device.online ? "is-online" : "is-offline"}`}>
      <header>
        <div className="device-icon"><Server size={19} /></div>
        <div className="device-title">
          <div><span className="status-dot" />{device.online ? "ONLINE" : "OFFLINE"}</div>
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
          disabled={!device.online || !effectiveReleaseId}
          onClick={() => onJob(device, "install_release", { release_id: effectiveReleaseId })}
        ><CloudDownload size={16} /> INSTALL</button>
      </div>
      <footer>
        <button disabled={!device.online} onClick={() => onJob(device, "mirror_now")}><Database size={14} /> MIRROR NOW</button>
        <button disabled={!device.online} onClick={() => onJob(device, "restart_takt")}><RotateCcw size={14} /> RESTART</button>
      </footer>
    </article>
  );
}

function JobRow({ job }) {
  const active = ["queued", "claimed", "running"].includes(job.status);
  return (
    <div className="job-row">
      <div className={`job-icon status-${job.status}`}>{active ? <RefreshCw size={15} /> : job.status === "succeeded" ? <Check size={15} /> : <X size={15} />}</div>
      <div className="job-copy">
        <strong>{job.action.replaceAll("_", " ")}</strong>
        <span>{job.device_name} · {job.message || job.status}</span>
      </div>
      <div className="job-progress"><i style={{ width: `${job.progress}%` }} /></div>
      <time>{timeAgo(job.updated_at)}</time>
    </div>
  );
}

function Dashboard({ session, refreshSession }) {
  const [devices, setDevices] = useState([]);
  const [releases, setReleases] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [modal, setModal] = useState(null);
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
  const logout = async () => {
    await request("/api/session", { method: "DELETE" }, session.csrf_token);
    await refreshSession();
  };
  const online = devices.filter((device) => device.online).length;
  const mirroredRuns = devices.reduce((sum, device) => sum + (device.run_count || 0), 0);
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
        {error && <div className="global-error"><WifiOff size={16} />{error}</div>}
        <section className="section-heading"><div><span>01 · APPLIANCES</span><h2>RASPBERRY PI FLEET</h2></div><button onClick={load}><RefreshCw size={14} /> REFRESH</button></section>
        <section className="device-grid">
          {devices.map((device) => <DeviceCard key={device.id} device={device} releases={releases} onJob={createJob} />)}
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
