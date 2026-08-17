// @ts-nocheck
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
import { preferredReleaseId } from "../releaseSelection.js";
import { bytes, timeAgo } from "../formatters";
import { HealthChecks } from "./HealthChecks";
import { MaintenancePanel } from "./MaintenancePanel";

export function DeviceCard({ device, releases, job, diagnostics, onJob, onCancel, onRetry, onForceClear, onRevoke, onWifi, onMaintenance, onAcknowledgeRecovery }) {
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
          <button className="secondary-button" onClick={() => onAcknowledgeRecovery(device)}>ACKNOWLEDGE</button>
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
            {installActive && <button className="secondary-button danger-action" onClick={() => onForceClear(job)}>FORCE CLEAR</button>}
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
