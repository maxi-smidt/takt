import { useState } from "react";
import {
  Activity,
  Ban,
  Clock3,
  CloudDownload,
  Database,
  Download,
  HardDrive,
  Radio,
  RotateCcw,
  Server,
  TriangleAlert,
  Wifi,
} from "lucide-react";
import type { Device, Job, Release } from "../../shared/contracts";
import { Button, Field, Select } from "../../shared/ui";
import { bytes, timeAgo } from "../formatters";
import { preferredReleaseId } from "../releaseSelection.js";
import { HealthChecks } from "./HealthChecks";
import { MaintenancePanel, type DiagnosticsBundle } from "./MaintenancePanel";

interface DeviceCardProps {
  device: Device;
  releases: Release[];
  job?: Job;
  diagnostics?: DiagnosticsBundle[];
  onJob: (device: Device, action: string, payload?: Record<string, unknown>) => void;
  onCancel: (job: Job) => void;
  onRetry: (job: Job) => void;
  onForceClear: (job: Job) => void;
  onRevoke: (device: Device) => void;
  onWifi: (device: Device) => void;
  onMaintenance: (device: Device, action: string) => void;
  onAcknowledgeRecovery: (device: Device) => void;
}

export function DeviceCard({
  device,
  releases,
  job,
  diagnostics,
  onJob,
  onCancel,
  onRetry,
  onForceClear,
  onRevoke,
  onWifi,
  onMaintenance,
  onAcknowledgeRecovery,
}: DeviceCardProps) {
  const [releaseId, setReleaseId] = useState(preferredReleaseId(releases));
  const effectiveReleaseId = releaseId || preferredReleaseId(releases);
  const status = device.status || {};
  const health = (status.health || {}) as { state?: string };
  const updateRecovery = (status.update_recovery as { stuck?: boolean; phase?: string; error?: string } | undefined)?.stuck
    ? (status.update_recovery as { phase?: string; error?: string })
    : null;
  const diskFree = status.disk_free_bytes as number | undefined;
  const protocolVersion = status.protocol_version as number | undefined;
  const protocolOk = protocolVersion === 1;
  const neverSeen = Object.keys(status).length === 0;
  const protocolLegacy = !neverSeen && !protocolOk;
  const wifiCapable = status.capabilities?.includes("wifi-profile-v1");
  const connectionParts = [
    status.registry_rtt_ms != null ? `${status.registry_rtt_ms} ms` : null,
    status.wifi_signal_dbm != null ? `${status.wifi_signal_dbm} dBm` : null,
    protocolVersion != null ? `protocol ${protocolVersion}` : "waiting for heartbeat",
    status.registry_transport === "insecure-http-opt-in" ? "HTTP opt-in" : (status.registry_transport as string | undefined),
  ].filter(Boolean);
  const installActive = job && ["queued", "claimed", "running"].includes(job.status);
  const installRetryable = job && ["rolled_back", "failed", "cancelled"].includes(job.status);
  const canCancel = installActive && !["activating", "restarting", "health_checking"].includes(job.stage ?? "");
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
          <Button variant="secondary" size="sm" className="recovery-row-action" onClick={() => onAcknowledgeRecovery(device)}>ACKNOWLEDGE</Button>
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
            {canCancel && <Button variant="secondary" size="sm" onClick={() => onCancel(job)}>CANCEL</Button>}
            {installActive && <Button variant="danger" size="sm" onClick={() => onForceClear(job)}>FORCE CLEAR</Button>}
            {installRetryable && <Button variant="secondary" size="sm" onClick={() => onRetry(job)}><RotateCcw size={14} /> RETRY</Button>}
          </div>
        </div>
      )}
      <div className="update-control">
        <Field label="INSTALL VERSION">
          {(fieldProps) => (
            <Select
              {...fieldProps}
              value={effectiveReleaseId}
              onValueChange={setReleaseId}
              placeholder="No releases uploaded"
              options={releases.map((release) => ({
                value: release.id,
                label: release.version + (release.source === "bundled" ? " · VERIFIED" : ""),
              }))}
            />
          )}
        </Field>
        <Button
          variant="primary"
          disabled={!device.online || protocolLegacy || !effectiveReleaseId || Boolean(updateRecovery) || Boolean(device.revoked_at) || Boolean(installActive)}
          title={protocolLegacy ? "This Pi needs a compatible Fleet agent before remote installs" : ""}
          onClick={() => onJob(device, "install_release", { release_id: effectiveReleaseId })}
        >
          <CloudDownload size={16} /> INSTALL
        </Button>
      </div>
      <HealthChecks healthChecks={(device as { health_checks?: unknown }).health_checks} />
      <MaintenancePanel device={device} diagnostics={diagnostics} onAction={onMaintenance} />
      <footer>
        <Button
          variant="secondary"
          disabled={!device.online || protocolLegacy || Boolean(updateRecovery) || Boolean(device.revoked_at)}
          title={protocolLegacy ? "This Pi needs a compatible Fleet agent before remote jobs" : ""}
          onClick={() => onJob(device, "mirror_now")}
        >
          <Database size={14} /> MIRROR NOW
        </Button>
        <Button
          variant="secondary"
          disabled={!device.online || !wifiCapable || Boolean(updateRecovery) || Boolean(device.revoked_at)}
          title={!wifiCapable ? "Rerun the Pi installer once to enable Fleet Wi-Fi" : ""}
          onClick={() => onWifi(device)}
        >
          <Wifi size={14} /> ADD WI-FI
        </Button>
        <Button variant="danger" disabled={Boolean(device.revoked_at)} onClick={() => onRevoke(device)}>
          <Ban size={14} /> REVOKE
        </Button>
      </footer>
    </article>
  );
}
