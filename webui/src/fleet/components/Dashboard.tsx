// @ts-nocheck
import { useState } from "react";
import {
  Archive,
  Box,
  LogOut,
  Plus,
  Radio,
  RefreshCw,
  Server,
  TriangleAlert,
  Upload,
  Wifi,
  WifiOff,
  Zap,
} from "lucide-react";
import { useFleetDashboard } from "../hooks/useFleetDashboard";
import { DeviceCard } from "./DeviceCard";
import { JobRow } from "./JobRow";
import { EnrollmentModal } from "./EnrollmentModal";
import { ReleaseModal } from "./ReleaseModal";
import { WifiModal } from "./WifiModal";
import { ConfirmModal } from "./ConfirmModal";
import { UserAdminPanel } from "./UserAdminPanel";

export function Dashboard({ session, refreshSession }) {
  const {
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
    acknowledgeRecovery,
    revokeDevice,
    logout,
  } = useFleetDashboard({ session, refreshSession });
  const [modal, setModal] = useState(null);
  const [wifiDevice, setWifiDevice] = useState(null);
  const [confirmation, setConfirmation] = useState(null);

  // Maintenance actions always go through the confirmation dialog, which is
  // also where an override for a busy timer is granted.
  const confirmMaintenance = async (override) => {
    const pending = confirmation;
    setConfirmation(null);
    if (pending) await submitJob(pending.device, pending.action, {}, override);
  };

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
          {devices.map((device) => <DeviceCard key={device.id} device={device} releases={releases} job={jobs.find((job) => job.device_id === device.id && job.action === "install_release")} diagnostics={diagnostics[device.id]} onJob={createJob} onCancel={cancelJob} onRetry={retryJob} onForceClear={forceClearJob} onRevoke={revokeDevice} onWifi={setWifiDevice} onMaintenance={(target, action) => setConfirmation({ device: target, action })} onAcknowledgeRecovery={acknowledgeRecovery} />)}
          {!devices.length && <div className="empty-card"><Server size={28} /><h3>NO DEVICES ENROLLED</h3><p>Start a guided deployment to connect the first Raspberry Pi.</p><button className="primary-button" onClick={() => setModal("enroll")}>ENROLL FIRST DEVICE</button></div>}
        </section>
        <section className="operations">
          <div className="section-heading"><div><span>02 · ACTIVITY</span><h2>DEPLOYMENT JOBS</h2></div></div>
          <div className="job-list">
            {jobs.slice(0, 12).map((job) => <JobRow key={job.id} job={job} onCancel={cancelJob} onRetry={retryJob} onForceClear={forceClearJob} />)}
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
