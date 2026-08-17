import { Check, LogOut, RefreshCw, Zap } from "lucide-react";
import type { SessionResponse } from "../../shared/contracts";
import { Button, Callout, IconButton } from "../../shared/ui";
import { formatIsoDate } from "../dateInput";
import { formatDate, formatDateTime, formatStopwatch, mirrorStateLabel } from "../formatters";
import { type PortalRun, usePortalRuns } from "../hooks/usePortalRuns";
import { DateField } from "./DateField";
import { PortalRunsChart } from "./PortalRunsChart";

const TIMEFRAMES = [
  { id: "day", label: "TAG" },
  { id: "year", label: "JAHR" },
  { id: "all", label: "GESAMT" },
];

function activeTimeframe(from: string, to: string) {
  const today = formatIsoDate(new Date());
  const yearStart = formatIsoDate(new Date(new Date().getFullYear(), 0, 1));
  if (from === today && to === today) return "day";
  if (from === yearStart && to === today) return "year";
  if (!from && !to) return "all";
  return null;
}

interface PortalProps {
  session: SessionResponse;
  refreshSession: () => Promise<void>;
}

export function Portal({ session, refreshSession }: PortalProps) {
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

  const applyTimeframe = (preset: string) => {
    const today = new Date();
    if (preset === "day") {
      const iso = formatIsoDate(today);
      setFrom(iso);
      setTo(iso);
    } else if (preset === "year") {
      setFrom(formatIsoDate(new Date(today.getFullYear(), 0, 1)));
      setTo(formatIsoDate(today));
    } else {
      setFrom("");
      setTo("");
    }
  };

  const currentTimeframe = activeTimeframe(from, to);
  const canWrite = devices.find((item) => item.id === deviceId)?.access === "write" || session.user?.is_admin;

  return (
    <div className="fleet-app portal-app">
      <header className="topbar">
        <div className="brand"><div className="brand-mark"><Zap size={18} /></div><strong>TAKT <em>LÄUFE</em></strong></div>
        <div className="top-actions">
          <span className="portal-username">{session.user?.username}</span>
          <IconButton variant="secondary" icon={<LogOut size={17} />} onClick={logout} aria-label="Abmelden" title="Abmelden" />
        </div>
      </header>
      <main>
        <section className="hero">
          <div>
            <span className="eyebrow">AUTORISIERTES LAUFPORTAL</span>
            <h1>LÄUFE</h1>
            <p>Hier findest du die gespeicherten Läufe deiner Buzzer. Sie werden automatisch aktuell gehalten.</p>
          </div>
        </section>
        {error && <Callout tone="danger">{error}</Callout>}
        <section className="section-heading">
          <div><span>GERÄTE</span><h2>IHRE TAKT-GERÄTE</h2></div>
          <Button variant="secondary" onClick={loadDevices}><RefreshCw size={14} /> AKTUALISIEREN</Button>
        </section>
        <section className="device-grid">
          {devices.map((device) => (
            <button className={"device-card portal-device " + (device.id === deviceId ? "selected" : "")} key={device.id} onClick={() => setDeviceId(device.id)}>
              <div className="portal-device-row">
                <strong>{device.name}</strong>
                {device.id === deviceId && <Check size={16} />}
              </div>
              <div className="portal-device-row">
                <span>{mirrorStateLabel(device.mirror_state)}</span>
                <span>{device.run_count ?? 0} Läufe</span>
              </div>
              <small>{formatDateTime(device.last_mirrored_at) || "Noch keine Daten"}</small>
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
              <span>{mirrorStateLabel(runs.mirror.state)} · {formatDateTime(runs.mirror.last_mirrored_at) || "noch nicht aktualisiert"}</span>
            </div>
            <div className="portal-filters">
              <div className="timeframe-group">
                {TIMEFRAMES.map((timeframe) => (
                  <Button
                    key={timeframe.id}
                    variant={currentTimeframe === timeframe.id ? "primary" : "ghost"}
                    className="timeframe-toggle"
                    onClick={() => applyTimeframe(timeframe.id)}
                  >
                    {timeframe.label}
                  </Button>
                ))}
              </div>
              <div className="portal-date-fields">
                <DateField label="VON" value={from} onChange={setFrom} />
                <DateField label="BIS" value={to} onChange={setTo} />
              </div>
            </div>
            <div className="summary-grid">
              <div><strong>{formatStopwatch(runs.summary.best_total_ms)}</strong><span>BESTZEIT</span></div>
              <div><strong>{formatStopwatch(Math.round(runs.summary.average_actual_ms || 0))}</strong><span>DURCHSCHNITT OHNE FEHLER</span></div>
              <div><strong>{formatStopwatch(Math.round(runs.summary.average_total_ms || 0))}</strong><span>DURCHSCHNITT MIT FEHLER</span></div>
            </div>
            <div className="portal-chart-panel">
              <PortalRunsChart runs={runs.runs} bestTotalMs={runs.summary.best_total_ms} />
            </div>
            <div className="runs-table-wrap">
              <table className="runs-table">
                <thead>
                  <tr>
                    <th>LAUF</th>
                    <th>DATUM</th>
                    <th>GESAMT</th>
                    <th>IST-ZEIT</th>
                    <th>FEHLER</th>
                    {canWrite && <th className="runs-actions-head">AKTIONEN</th>}
                  </tr>
                </thead>
                <tbody>
                  {runs.runs.map((run: PortalRun) => (
                    <tr key={run.id}>
                      <td>{run.run_number}</td>
                      <td>{formatDate(run.session_date)}</td>
                      <td>{formatStopwatch(run.total_time_ms)}</td>
                      <td>{formatStopwatch(run.actual_time_ms)}</td>
                      <td>+{formatStopwatch(run.added_time_ms)}</td>
                      {canWrite && (
                        <td className="runs-actions">
                          <div className="runs-actions-buttons">
                            <Button variant="secondary" size="sm" onClick={() => command(run, "adjust_added_time", Math.max(0, run.added_time_ms + 5000))}>+5 s</Button>
                            <Button variant="secondary" size="sm" onClick={() => command(run, "delete")}>LÖSCHEN</Button>
                          </div>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
              {!runs.runs.length && <div className="jobs-empty">Keine Läufe im gewählten Zeitraum.</div>}
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
