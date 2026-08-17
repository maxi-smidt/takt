// @ts-nocheck
import { LogOut, RefreshCw, Zap } from "lucide-react";
import { usePortalRuns } from "../hooks/usePortalRuns";
import { formatDate, formatDateTime, formatStopwatch, mirrorStateLabel } from "../formatters";

export function Portal({ session, refreshSession }) {
  const {
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
  } = usePortalRuns({ session, refreshSession });

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
