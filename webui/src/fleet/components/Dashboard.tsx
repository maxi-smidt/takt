import { useState } from "react";
import {
  Archive,
  Box,
  Flag,
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
import type { Device, Job } from "../../shared/contracts";
import { Button, Callout, IconButton } from "../../shared/ui";
import { useFleetDashboard } from "../hooks/useFleetDashboard";
import { ConfirmModal } from "./ConfirmModal";
import { DeviceCard } from "./DeviceCard";
import { EnrollmentModal } from "./EnrollmentModal";
import { JobRow } from "./JobRow";
import { ReleaseModal } from "./ReleaseModal";
import { UserAdminPanel } from "./UserAdminPanel";
import { WifiModal } from "./WifiModal";

interface DashboardProps {
  session: { csrf_token: string };
  refreshSession: () => Promise<void>;
  onSwitchToRuns?: () => void;
}

const ACTIVE_JOB_STATUSES = new Set(["queued", "claimed", "running"]);

// Active jobs surface above finished ones so an operator glancing at the
// panel sees what's still in flight first; Array.prototype.sort is stable,
// so each group keeps the server's newest-first ordering.
function sortJobsActiveFirst(jobs: Job[]): Job[] {
  return [...jobs].sort(
    (a, b) => Number(ACTIVE_JOB_STATUSES.has(b.status)) - Number(ACTIVE_JOB_STATUSES.has(a.status))
  );
}

export function Dashboard({ session, refreshSession, onSwitchToRuns }: DashboardProps) {
  const {
    devices,
    releases,
    bundledRelease,
    jobs,
    diagnostics,
    error,
    refreshing,
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
  } = useFleetDashboard({ session, refreshSession });
  const [modal, setModal] = useState<"enroll" | "release" | null>(null);
  const [wifiDevice, setWifiDevice] = useState<Device | null>(null);
  const [confirmation, setConfirmation] = useState<{ device: Device; action: string } | null>(null);

  // Maintenance actions always go through the confirmation dialog, which is
  // also where an override for a busy timer is granted.
  const confirmMaintenance = async (override: boolean) => {
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
          <Button variant="secondary" onClick={() => setModal("enroll")}><Plus size={15} /> ENROLL DEVICE</Button>
          <Button variant="secondary" onClick={() => setModal("release")}><Upload size={15} /> ADD RELEASE</Button>
          {onSwitchToRuns && (
            <Button variant="secondary" onClick={onSwitchToRuns}><Flag size={15} /> VIEW RUNS</Button>
          )}
          <IconButton variant="secondary" icon={<LogOut size={17} />} onClick={logout} aria-label="Log out" title="Log out" />
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
        {insecureLan && (
          <Callout tone="warning">
            <strong>UNENCRYPTED REGISTRY</strong> Use HTTPS or a private Tailscale/WireGuard network before installing releases or operating outside an isolated LAN.
          </Callout>
        )}
        {bundledRelease?.status === "error" && (
          <Callout tone="danger">
            <strong>BUNDLED RELEASE UNAVAILABLE</strong> {bundledRelease.detail || `Reason: ${bundledRelease.reason}`}. Upload a release manually until this image is rebuilt.
          </Callout>
        )}
        {error && <Callout tone="danger">{error}</Callout>}
        <section className="section-heading"><div><span>01 · APPLIANCES</span><h2>RASPBERRY PI FLEET</h2></div><Button variant="secondary" onClick={load}><RefreshCw size={14} className={refreshing ? "is-spinning" : undefined} /> REFRESH</Button></section>
        <section className="device-grid">
          {devices.map((device) => <DeviceCard key={device.id} device={device} releases={releases} job={jobs.find((job) => job.device_id === device.id && job.action === "install_release")} diagnostics={diagnostics[device.id]} onJob={createJob} onCancel={cancelJob} onRetry={retryJob} onForceClear={forceClearJob} onDelete={deleteJob} onRevoke={revokeDevice} onWifi={setWifiDevice} onMaintenance={(target, action) => setConfirmation({ device: target, action })} onAcknowledgeRecovery={acknowledgeRecovery} />)}
          {!devices.length && (
            <div className="empty-card">
              <Server size={28} />
              <h3>NO DEVICES ENROLLED</h3>
              <p>Start a guided deployment to connect the first Raspberry Pi.</p>
              <Button variant="primary" onClick={() => setModal("enroll")}>ENROLL FIRST DEVICE</Button>
            </div>
          )}
        </section>
        <section className="operations">
          <div className="section-heading"><div><span>02 · ACTIVITY</span><h2>DEPLOYMENT JOBS</h2></div></div>
          <div className="job-list">
            {sortJobsActiveFirst(jobs).slice(0, 12).map((job) => <JobRow key={job.id} job={job} onCancel={cancelJob} onRetry={retryJob} onForceClear={forceClearJob} onDelete={deleteJob} />)}
            {!jobs.length && <div className="jobs-empty">No remote operations have been requested.</div>}
          </div>
        </section>
        <UserAdminPanel csrf={session.csrf_token} devices={devices} />
      </main>
      {modal === "enroll" && <EnrollmentModal csrf={session.csrf_token} releases={releases} onDone={load} onClose={() => setModal(null)} />}
      {modal === "release" && <ReleaseModal csrf={session.csrf_token} releases={releases} onClose={() => setModal(null)} onUploaded={load} onUninstall={uninstallRelease} />}
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
