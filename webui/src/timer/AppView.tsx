// @ts-nocheck
// The feature view is intentionally kept behaviorally identical during the staged module migration.
import {
  Activity,
  Bluetooth,
  Cable,
  CalendarDays,
  Check,
  ChevronRight,
  CirclePower,
  Download,
  Clock3,
  Expand,
  Gauge,
  Minus,
  MonitorUp,
  Network,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Settings,
  ShieldCheck,
  TimerReset,
  Trash2,
  Trophy,
  Volume2,
  WifiOff,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button, IconButton, Select } from "../shared/ui";
import { useScreenAwake } from "../useScreenAwake";
import { useTaktServer } from "../useTaktServer";

const STATE_META = {
  ready: {
    index: "01",
    title: "BEREIT",
    subtitle: "WARTET AUF START",
    hint: "Taste oder Leertaste zum Starten drücken",
  },
  running: {
    index: "02",
    title: "LÄUFT",
    subtitle: "ZEITMESSUNG AKTIV",
    hint: "Taste oder Leertaste zum Stoppen drücken",
  },
  stopped: {
    index: "03",
    title: "GESTOPPT",
    subtitle: "ZEIT PRÜFEN UND WERTEN",
    hint: "Zuschlag anpassen, anschließend speichern oder verwerfen",
  },
  saved_confirmation: {
    index: "04",
    title: "GESPEICHERT",
    subtitle: "LAUF WURDE ÜBERNOMMEN",
    hint: "Bereit für den nächsten Lauf",
  },
  discard_confirmation: {
    index: "05",
    title: "VERWERFEN?",
    subtitle: "DIESE ZEIT NICHT SPEICHERN",
    hint: "Verwerfen bestätigen oder mit Esc abbrechen",
  },
  error: {
    index: "!",
    title: "FEHLER",
    subtitle: "AKTION NICHT MÖGLICH",
    hint: "",
  },
};

const PERIODS = [
  ["7", "7 TAGE"],
  ["30", "30 TAGE"],
  ["90", "90 TAGE"],
  ["all", "ALLE"],
];

const FOCUSABLE_SELECTOR = "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex='-1'])";

function useDialogFocus(open, dialogRef) {
  const previousFocus = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    previousFocus.current = document.activeElement;
    const dialog = dialogRef.current;
    const focusables = () => [...(dialog?.querySelectorAll(FOCUSABLE_SELECTOR) || [])];
    queueMicrotask(() => focusables()[0]?.focus());
    const onKeyDown = (event) => {
      if (event.key !== "Tab" || !dialog) return;
      const items = focusables();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previousFocus.current?.focus?.();
    };
  }, [dialogRef, open]);
}

function Brand() {
  return (
    <div className="brand" aria-label="TAKT Feuerwehr-Zeitnahme">
      <div className="brand-copy">
        <strong>TAKT</strong>
        <span>FEUERWEHR · ZEITNAHME</span>
      </div>
    </div>
  );
}

function ConnectionStatus({ status }) {
  const content = {
    online: [Network, "LIVE VERBUNDEN"],
    connecting: [Activity, "VERBINDUNG …"],
    offline: [WifiOff, "OFFLINE"],
  };
  const [Icon, label] = content[status] || content.offline;
  return (
    <div className={`connection connection-${status}`} role="status" aria-live="polite">
      <Icon size={13} strokeWidth={2.4} />
      <span>{label}</span>
    </div>
  );
}

function Header({ connection, dateText, onOpenSettings, settingsDisabled }) {
  return (
    <header className="topbar">
      <Brand />
      <div className="topbar-right">
        <ConnectionStatus status={connection} />
        <div className="date-readout">
          <CalendarDays size={14} />
          <span>{dateText}</span>
        </div>
        <IconButton
          variant="secondary"
          className="icon-button settings-trigger"
          icon={<Settings size={18} />}
          onClick={onOpenSettings}
          disabled={settingsDisabled}
          aria-label="Einstellungen öffnen"
        />
      </div>
    </header>
  );
}

function PanelHeader({ icon: Icon, eyebrow, title, aside }) {
  return (
    <div className="panel-header">
      <div className="panel-heading">
        <Icon size={15} strokeWidth={2.3} />
        <div>
          {eyebrow && <span>{eyebrow}</span>}
          <h2>{title}</h2>
        </div>
      </div>
      {aside}
    </div>
  );
}

function StopwatchValue({ value, hero = false }) {
  return (
    <div className={`stopwatch ${hero ? "stopwatch-hero" : ""}`}>
      <span>{value}</span>
      <div className="time-units" aria-hidden="true">
        <i>MIN</i><i>SEK</i><i>HS</i>
      </div>
    </div>
  );
}

function ActionButton({ icon: Icon, children, className = "", ...props }) {
  return (
    <Button variant="secondary" className={`action-button ${className}`} {...props}>
      {Icon && <Icon size={16} strokeWidth={2.4} />}
      <span>{children}</span>
    </Button>
  );
}

function ReadyTimer({ state, onPrimary, pending }) {
  const sequence = state.start_sequence;
  const maintenance = state.maintenance?.held;
  if (sequence?.active) {
    const waiting = sequence.phase === "waiting";
    const seconds = Math.max(0, (sequence.remaining_ms || 0) / 1000).toFixed(3);
    return (
      <div className="start-sequence" role="status" aria-live="polite">
        <Volume2 size={34} strokeWidth={1.8} />
        <strong>{waiting ? seconds : "SIGNAL"}</strong>
        <span>{waiting ? "SEKUNDEN BIS ZUM START" : "AUDIO WIRD VORBEREITET"}</span>
      </div>
    );
  }
  return (
    <button className="timer-hit-area" type="button" onClick={onPrimary} disabled={maintenance || pending} aria-busy={pending || undefined}>
      <StopwatchValue value={state.actual} hero />
    </button>
  );
}

function ResultTimer({ state, onAction, pending }) {
  const isStopped = state.state === "stopped";
  const isDiscard = state.state === "discard_confirmation";
  const isSaved = state.state === "saved_confirmation";
  return (
    <div className="result-view">
      {isSaved && (
        <div className="saved-seal" aria-hidden="true">
          <Check size={25} strokeWidth={2.5} />
        </div>
      )}
      <div className="result-total">
        <span>GESAMTZEIT</span>
        <strong>{state.total}</strong>
      </div>
      <div className="result-breakdown">
        <div>
          <span>IST-ZEIT</span>
          <strong>{state.actual}</strong>
        </div>
        <i />
        <div>
          <span>ZUSCHLAG</span>
          <strong className="value-penalty">{state.added}</strong>
        </div>
      </div>
      {isStopped && (
        <>
          <div className="adjustment-strip">
            <span className="adjustment-label">ZUSCHLAG</span>
            <div className="adjustment-buttons">
              <button
                type="button"
                onClick={() => onAction("subtract_10")}
                disabled={pending || state.added_ms <= 0}
              >
                <Minus size={13} />10
              </button>
              <button
                type="button"
                onClick={() => onAction("subtract_5")}
                disabled={pending || state.added_ms <= 0}
              >
                <Minus size={13} />5
              </button>
              <button type="button" onClick={() => onAction("add_5")} disabled={pending}>
                <Plus size={13} />5
              </button>
              <button type="button" onClick={() => onAction("add_10")} disabled={pending}>
                <Plus size={13} />10
              </button>
            </div>
          </div>
          <div className="primary-actions">
            <ActionButton
              icon={Save}
              className="action-save"
              onClick={() => onAction("save")}
              disabled={pending}
            >
              SPEICHERN <kbd>ENTER</kbd>
            </ActionButton>
            <ActionButton
              icon={RotateCcw}
              className="action-danger"
              disabled={pending}
              onClick={() => onAction("request_discard")}
            >
              VERWERFEN <kbd>R</kbd>
            </ActionButton>
          </div>
        </>
      )}
      {isDiscard && (
        <div className="primary-actions">
          <ActionButton icon={X} onClick={() => onAction("cancel_discard")} disabled={pending}>
            ABBRECHEN <kbd>ESC</kbd>
          </ActionButton>
          <ActionButton
            icon={RotateCcw}
            className="action-danger"
            onClick={() => onAction("confirm_discard")}
            disabled={pending}
          >
            VERWERFEN <kbd>ENTER</kbd>
          </ActionButton>
        </div>
      )}
    </div>
  );
}

function TimerPanel({ state, screenAwake, onAction, pending }) {
  const startSequence = state.start_sequence;
  const meta = startSequence?.active
    ? {
        index: "01",
        title: "STARTSIGNAL",
        subtitle: startSequence.phase === "waiting"
          ? "TON LÄUFT · STARTVERZÖGERUNG"
          : "AUDIO WIRD VORBEREITET",
        hint: "Bitte warten – die Zeitmessung startet automatisch",
      }
    : STATE_META[state.state] || STATE_META.error;
  const basic = state.state === "ready" || state.state === "running";
  const maintenance = state.maintenance?.held;
  return (
    <section
      className={`instrument-panel timer-panel state-${state.state} ${
        startSequence?.active ? "is-start-sequence" : ""
      }`}
    >
      <div className="timer-panel-top">
        <span className="stage-index">{meta.index}</span>
        <div className="stage-copy">
          <strong>{meta.title}</strong>
          <span>{meta.subtitle}</span>
        </div>
        <div className="stage-indicator"><i /></div>
        {state.state === "running" && (
          <div
            className={`screen-awake-indicator ${screenAwake === "active" ? "is-active" : ""}`}
            role="status"
            title={screenAwake === "active"
              ? "Der Bildschirm bleibt aktiv."
              : "Tippen oder Taste drücken, um den Bildschirm aktiv zu halten."}
          >
            <MonitorUp size={14} />
            <span>{screenAwake === "active" ? "ANZEIGE AKTIV" : "ANZEIGE ANTIPPEN"}</span>
          </div>
        )}
      </div>
      <div className="timer-panel-body">
        {maintenance && state.state === "ready" && (
          <div className="maintenance-banner" role="status" aria-live="polite">
            <RefreshCw size={22} />
            <strong>WARTUNG LÄUFT</strong>
            <span>{state.maintenance.reason || "TAKT wird gerade sicher aktualisiert."}</span>
          </div>
        )}
        {basic ? (
          <ReadyTimer state={state} pending={pending} onPrimary={() => onAction("primary")} />
        ) : (
          <ResultTimer state={state} pending={pending} onAction={onAction} />
        )}
      </div>
      <div className="timer-panel-foot">
        <TimerReset size={14} />
        <span>{maintenance ? "Start ist während der Wartung gesperrt" : startSequence?.error || state.error || meta.hint}</span>
      </div>
    </section>
  );
}

function EmptyState({ children }) {
  return (
    <div className="empty-state">
      <Clock3 size={22} strokeWidth={1.5} />
      <span>{children}</span>
    </div>
  );
}

function RunsTable({ rows, type }) {
  if (!rows.length) {
    return <EmptyState>Noch keine Läufe gespeichert</EmptyState>;
  }
  return (
    <div className="table-viewport">
      <table className="runs-table">
        <thead>
          <tr>
            <th>#</th>
            <th>{type === "today" ? "UHRZEIT" : "DATUM"}</th>
            <th>IST-ZEIT</th>
            <th>ZUSCHLAG</th>
            <th>GESAMT</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((run) => (
            <tr key={run.id}>
              <td className="run-number">{type === "best" ? run.rank : run.number}</td>
              <td>{type === "today" ? run.time : run.date}</td>
              <td>{run.actual}</td>
              <td className="penalty-cell">{run.added}</td>
              <td className="total-cell">{run.total}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TodayPanel({ history }) {
  return (
    <section className="instrument-panel today-panel">
      <PanelHeader
        icon={Gauge}
        eyebrow="AKTUELLE SESSION"
        title="HEUTIGE LÄUFE"
        aside={<div className="run-count"><strong>{history.today_count}</strong><span>LÄUFE</span></div>}
      />
      <RunsTable rows={history.today} type="today" />
    </section>
  );
}

function BestPanel({ history }) {
  return (
    <section className="instrument-panel best-panel">
      <PanelHeader icon={Trophy} eyebrow="ARCHIV" title="BESTZEITEN" />
      <RunsTable rows={history.best} type="best" />
    </section>
  );
}

function formatAxis(value) {
  const totalSeconds = Math.round(value / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function browserBeep() {
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    const audio = new AudioContext();
    const oscillator = audio.createOscillator();
    const gain = audio.createGain();
    oscillator.frequency.value = 720;
    gain.gain.setValueAtTime(0.055, audio.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + 0.085);
    oscillator.connect(gain).connect(audio.destination);
    oscillator.start();
    oscillator.stop(audio.currentTime + 0.085);
  } catch {
    // Some browsers allow audio only after the first direct interaction.
  }
}

function ChartPanel({ history, chartDays, onPeriodChange }) {
  const data = useMemo(
    () => history.chart.map((run) => ({
      ...run,
      label: `${run.date_short} ${run.time.slice(0, 5)}`,
    })),
    [history.chart],
  );
  const chart = useMemo(() => {
    const width = 760;
    const height = 230;
    const padding = { left: 56, right: 18, top: 16, bottom: 31 };
    const innerWidth = width - padding.left - padding.right;
    const innerHeight = height - padding.top - padding.bottom;
    const maximum = Math.max(10000, ...data.map((run) => run.total_ms)) * 1.15;
    const x = (index) => (
      padding.left + (data.length === 1 ? innerWidth / 2 : index * innerWidth / (data.length - 1))
    );
    const y = (milliseconds) => padding.top + innerHeight - (milliseconds / maximum) * innerHeight;
    const points = data.map((run, index) => ({
      ...run,
      x: x(index),
      actualY: y(run.actual_ms),
      totalY: y(run.total_ms),
    }));
    return {
      width,
      height,
      padding,
      innerWidth,
      innerHeight,
      maximum,
      points,
    };
  }, [data]);
  // Evenly spaced ticks plus the final point. The last regular tick is dropped
  // when it would sit close enough to the final one for the labels to overlap.
  const labelIndices = useMemo(() => {
    const total = chart.points.length;
    if (!total) return new Set();
    const step = Math.max(1, Math.ceil(total / 5));
    const indices = [];
    for (let index = 0; index < total - 1; index += step) indices.push(index);
    if (indices.length && total - 1 - indices.at(-1) < step / 2) indices.pop();
    indices.push(total - 1);
    return new Set(indices);
  }, [chart.points.length]);
  const switcher = (
    <div className="period-switch">
      {PERIODS.map(([value, label]) => (
        <Button
          key={value}
          variant="secondary"
          data-short={value === "all" ? "ALLE" : value}
          aria-pressed={chartDays === value}
          className={chartDays === value ? "is-active" : ""}
          onClick={() => onPeriodChange(value)}
        >
          {label}
        </Button>
      ))}
    </div>
  );
  return (
    <section className="instrument-panel chart-panel">
      <PanelHeader icon={Activity} eyebrow="GESAMTZEIT" title="LEISTUNGSVERLAUF" aside={switcher} />
      <div className="chart-area">
        {data.length ? (
          <svg
            className="performance-chart"
            viewBox={`0 0 ${chart.width} ${chart.height}`}
            role="img"
            aria-label="Verlauf von Ist- und Gesamtzeiten"
          >
            {[0, 1, 2, 3, 4].map((step) => {
              const lineY = chart.padding.top + (step * chart.innerHeight) / 4;
              const value = chart.maximum * (1 - step / 4);
              return (
                <g key={step}>
                  <line
                    x1={chart.padding.left}
                    x2={chart.padding.left + chart.innerWidth}
                    y1={lineY}
                    y2={lineY}
                    className="chart-gridline"
                  />
                  <text x={chart.padding.left - 8} y={lineY + 3} className="chart-axis-label">
                    {formatAxis(value)}
                  </text>
                </g>
              );
            })}
            {chart.points.map((point) => (
              <line
                key={`penalty-${point.id}`}
                x1={point.x}
                x2={point.x}
                y1={point.actualY}
                y2={point.totalY}
                className="chart-penalty-line"
              />
            ))}
            <polyline
              points={chart.points.map((point) => `${point.x},${point.actualY}`).join(" ")}
              className="chart-line chart-line-actual"
            />
            <polyline
              points={chart.points.map((point) => `${point.x},${point.totalY}`).join(" ")}
              className="chart-line chart-line-total"
            />
            {chart.points.map((point, index) => {
              const lastIndex = chart.points.length - 1;
              const showLabel = labelIndices.has(index);
              const tooltip = (
                `${point.date} · ${point.time}\n`
                + `Ist-Zeit: ${point.actual}\nZuschlag: ${point.added}\nGesamt: ${point.total}`
              );
              return (
                <g key={point.id}>
                  <circle cx={point.x} cy={point.actualY} r="3" className="chart-point actual-point">
                    <title>{tooltip}</title>
                  </circle>
                  <circle cx={point.x} cy={point.totalY} r="3" className="chart-point total-point">
                    <title>{tooltip}</title>
                  </circle>
                  {showLabel && (
                    <text
                      x={point.x}
                      y={chart.padding.top + chart.innerHeight + 21}
                      className={`chart-x-label ${
                        index === 0 ? "is-first" : index === lastIndex ? "is-last" : ""
                      }`}
                    >
                      {point.label}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        ) : (
          <EmptyState>Noch keine Verlaufsdaten</EmptyState>
        )}
      </div>
      <div className="chart-legend">
        <span className="legend-actual"><i />IST-ZEIT</span>
        <span className="legend-penalty"><i />ZUSCHLAG</span>
        <span className="legend-total"><i />GESAMTZEIT</span>
      </div>
    </section>
  );
}

function Footer({ state, system, screenAwake, onMockPress, pending }) {
  return (
    <footer className="statusbar">
      <div className={`hardware-readout ${state.hardware.available ? "is-online" : ""}`}>
        <i />
        <span>TASTER</span>
        <strong>{state.hardware.label}</strong>
      </div>
      {system.mock_buzzer && (
        <div className="hardware-readout">
          <i />
          <span>SUMMER-MOCK</span>
          <strong>BEREIT</strong>
        </div>
      )}
      <div
        className={`hardware-readout ${screenAwake === "active" ? "is-online" : ""}`}
        title={screenAwake === "active"
          ? "Der Bildschirm bleibt aktiv, solange TAKT geöffnet ist."
          : "Einmal tippen oder eine Taste drücken, um den Bildschirm aktiv zu halten."}
      >
        <i />
        <span>ANZEIGE</span>
        <strong>{screenAwake === "active" ? "BLEIBT AKTIV" : "ANTIPPEN ZUM AKTIVIEREN"}</strong>
      </div>
      <div className="statusbar-spacer" />
      <div className="local-url">
        <ShieldCheck size={13} />
        <span>{location.protocol === "file:" ? "LOKALE VORSCHAU" : location.host}</span>
      </div>
      {system.mock_button && (
        <button className="mock-trigger" type="button" onClick={onMockPress} disabled={pending}>
          MOCK-TASTER
          <ChevronRight size={14} />
        </button>
      )}
    </footer>
  );
}

function AudioSettingsPanel({ audio, onRequest }) {
  const [draft, setDraft] = useState(audio);
  const [selectedAddress, setSelectedAddress] = useState(audio.device_address || "");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    queueMicrotask(() => {
      setDraft(audio);
      setSelectedAddress(audio.device_address || "");
    });
  }, [audio]);

  const request = async (operation, payload, successMessage) => {
    setBusy(operation);
    setMessage("");
    try {
      await onRequest(operation, payload);
      setMessage(successMessage);
    } catch (error) {
      setMessage(error.message);
      throw error;
    } finally {
      setBusy("");
    }
  };

  const settingsPayload = {
    enabled: draft.output !== "off",
    output: draft.output,
    delay_milliseconds: Number(draft.delay_milliseconds),
    device_address: selectedAddress || null,
    device_name: (
      audio.devices.find((device) => device.address === selectedAddress)?.name
      || draft.device_name
      || null
    ),
  };

  const save = async () => {
    try {
      await request(
        "settings",
        settingsPayload,
        draft.output === "off"
          ? "Startsignal ist ausgeschaltet."
          : "Audio-Einstellung gespeichert.",
      );
    } catch {
      // The German server message is displayed inside the settings panel.
    }
  };

  const test = async () => {
    setBusy("test");
    setMessage("");
    try {
      await onRequest("settings", settingsPayload);
      await onRequest("test");
      setMessage("Testton wurde abgespielt.");
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy("");
    }
  };

  const connect = async () => {
    if (!selectedAddress) return;
    try {
      await request(
        "connect",
        { address: selectedAddress },
        "Bluetooth-Lautsprecher ist verbunden.",
      );
    } catch {
      // The German server message is displayed inside the settings panel.
    }
  };

  const forget = async () => {
    if (!selectedAddress) return;
    try {
      await request(
        "forget",
        { address: selectedAddress },
        "Lautsprecher wurde entfernt.",
      );
      setSelectedAddress("");
    } catch {
      // The German server message is displayed inside the settings panel.
    }
  };

  const scanning = busy === "scan" || audio.scanning;

  return (
    <>
      <div className="data-section-header audio-section-header">
        <div>
          <span>STARTSIGNAL</span>
          <h3>LAUTSPRECHER UND STARTVERZÖGERUNG</h3>
        </div>
        <p>Der Timer startet während des Tons nach der eingestellten Wartezeit.</p>
      </div>
      <section className="audio-settings">
        <div className="audio-mode-switch" aria-label="Audio-Ausgang">
          {[
            ["off", Volume2, "AUS"],
            ["aux", Cable, "AUX"],
            ["bluetooth", Bluetooth, "BLUETOOTH"],
          ].map(([value, Icon, label]) => (
            <Button
              key={value}
              variant="secondary"
              className={draft.output === value ? "is-active" : ""}
              aria-pressed={draft.output === value}
              onClick={() => setDraft((current) => ({ ...current, output: value }))}
            >
              <Icon size={16} />{label}
            </Button>
          ))}
        </div>
        <label className="audio-delay">
          <span>START NACH</span>
          <div>
            <input
              type="number"
              min="0"
              max={audio.clip_duration_milliseconds}
              step="1"
              value={draft.delay_milliseconds}
              onChange={(event) => setDraft((current) => ({
                ...current,
                delay_milliseconds: event.target.value,
              }))}
            />
            <strong>MS</strong>
          </div>
          <small>
            {(Number(draft.delay_milliseconds || 0) / 1000).toFixed(3)} s · maximal{" "}
            {(audio.clip_duration_milliseconds / 1000).toFixed(3)} s
          </small>
        </label>
        <div className="audio-summary">
          <span>TONDATEI</span>
          <strong>{audio.sound}</strong>
          <small>
            {audio.playback_available
              ? (
                  `${(audio.clip_duration_milliseconds / 1000).toFixed(3)} s`
                  + `${audio.player ? ` · ${audio.player}` : ""}`
                )
              : "Kein Audioplayer gefunden"}
          </small>
        </div>
        {draft.output === "aux" && (
          <div className="audio-note">
            <Cable size={17} />
            <span>AUX-Kabel einstecken. TAKT verwendet den analogen Systemausgang.</span>
          </div>
        )}
        {draft.output === "bluetooth" && (
          <div className="bluetooth-controls">
            <div className="bluetooth-device-row">
              <Select
                className="bluetooth-select"
                value={selectedAddress}
                onValueChange={setSelectedAddress}
                disabled={!audio.bluetooth_available || Boolean(busy)}
                aria-label="Bluetooth-Lautsprecher"
                placeholder="Lautsprecher auswählen …"
                options={[
                  ...(selectedAddress && !audio.devices.some((device) => device.address === selectedAddress)
                    ? [{ value: selectedAddress, label: draft.device_name || selectedAddress }]
                    : []),
                  ...audio.devices.map((device) => ({
                    value: device.address,
                    label: device.name + (device.connected ? " · verbunden" : device.paired ? " · gekoppelt" : ""),
                  })),
                ]}
              />
              <Button
                variant="secondary"
                disabled={!audio.bluetooth_available || Boolean(busy)}
                onClick={() => request("scan", {}, "Bluetooth-Suche abgeschlossen.").catch(() => {})}
              >
                <RefreshCw size={14} />{scanning ? "SUCHE …" : "SUCHEN"}
              </Button>
              <Button
                variant="secondary"
                disabled={!selectedAddress || Boolean(busy)}
                onClick={connect}
              >
                <Bluetooth size={14} />{busy === "connect" ? "VERBINDE …" : "VERBINDEN"}
              </Button>
              <Button
                variant="danger"
                className="bluetooth-forget"
                disabled={!selectedAddress || Boolean(busy)}
                onClick={forget}
              >
                <Trash2 size={14} />{busy === "forget" ? "ENTFERNE …" : "ENTFERNEN"}
              </Button>
            </div>
            {audio.scanning && (
              <div className="audio-note">
                <RefreshCw size={17} />
                <span>Bluetooth-Suche läuft im Hintergrund weiter, neue Geräte erscheinen automatisch.</span>
              </div>
            )}
            {!audio.bluetooth_available && (
              <div className="audio-note is-warning">
                Bluetooth-Verwaltung ist nur auf einem eingerichteten Raspberry Pi verfügbar.
              </div>
            )}
            {audio.bluetooth_available && (
              <div className="audio-note">
                <Bluetooth size={17} />
                <span>
                  Bereits gekoppelte Lautsprecher werden direkt verbunden, ohne sie neu zu koppeln.
                </span>
              </div>
            )}
          </div>
        )}
        <div className="audio-settings-footer">
          <div className="audio-feedback">{message}</div>
          <Button
            variant="secondary"
            onClick={test}
            disabled={draft.output === "off" || !audio.playback_available || Boolean(busy)}
          >
            <Volume2 size={14} />{busy === "test" ? "SPIELE …" : "TESTTON"}
          </Button>
          <Button variant="primary" className="audio-save" onClick={save} disabled={Boolean(busy)}>
            <Save size={14} />ÜBERNEHMEN
          </Button>
        </div>
      </section>
    </>
  );
}

function SettingsModal({
  open,
  onClose,
  history,
  system,
  selectedRunId,
  onSelectRun,
  onPrepare,
  onAudioRequest,
  onExport,
  exportBusy,
  pending,
  blocked = false,
  feedback,
}) {
  const dialogRef = useRef(null);
  const [fullscreen, setFullscreen] = useState(Boolean(document.fullscreenElement));
  useEffect(() => {
    const update = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", update);
    return () => document.removeEventListener("fullscreenchange", update);
  }, []);
  useDialogFocus(open && !blocked, dialogRef);
  if (!open) return null;

  const toggleFullscreen = async () => {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  };
  return (
    <div className="modal-backdrop" role="presentation" aria-hidden={blocked || undefined} inert={blocked || undefined} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section
        ref={dialogRef}
        className="settings-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal-header">
          <div>
            <span>KONFIGURATION</span>
            <h2 id="settings-title">EINSTELLUNGEN</h2>
          </div>
          <IconButton variant="secondary" className="icon-button" icon={<X size={19} />} onClick={onClose} aria-label="Schließen" />
        </header>
        <div className="settings-system-grid">
          <div className="system-tile">
            <MonitorUp size={18} />
            <div><span>ANZEIGE</span><strong>Browser auf diesem Gerät</strong></div>
            <Button variant="secondary" onClick={toggleFullscreen}>
              <Expand size={14} />{fullscreen ? "VOLLBILD BEENDEN" : "VOLLBILD"}
            </Button>
          </div>
          <div className="system-tile">
            <CirclePower size={18} />
            <div><span>SYSTEM</span><strong>{system.model || "Lokaler TAKT-Server"}</strong></div>
            <Button
              variant="danger"
              className="shutdown-control"
              disabled={!system.shutdown_available || pending.confirmation}
              onClick={() => onPrepare("shutdown")}
            >
              HERUNTERFAHREN
            </Button>
          </div>
          <div className="system-tile export-tile">
            <Download size={18} />
            <div>
              <span>DATENEXPORT</span>
              <strong>DB oder CSV herunterladen</strong>
            </div>
            <div className="export-actions">
              <Button
                variant="secondary"
                disabled={Boolean(exportBusy) || pending.export}
                onClick={() => onExport("db")}
              >
                <Download size={14} />{exportBusy === "db" ? "LÄDT …" : "DATENBANK (.DB)"}
              </Button>
              <Button
                variant="secondary"
                disabled={Boolean(exportBusy) || pending.export}
                onClick={() => onExport("csv")}
              >
                <Download size={14} />{exportBusy === "csv" ? "LÄDT …" : "LÄUFE (.CSV)"}
              </Button>
            </div>
          </div>
        </div>

        <AudioSettingsPanel audio={system.audio} onRequest={onAudioRequest} />
        <div className="data-section-header">
          <div>
            <span>DATENPFLEGE</span>
            <h3>GESPEICHERTE LÄUFE</h3>
          </div>
          <p>Ist-Zeiten bleiben unverändert. Jede Änderung muss bestätigt werden.</p>
        </div>
        <div className="settings-table-shell">
          <table className="settings-table">
            <thead>
              <tr>
                <th>#</th><th>DATUM</th><th>UHRZEIT</th><th>IST-ZEIT</th><th>ZUSCHLAG</th><th>GESAMT</th>
              </tr>
            </thead>
            <tbody>
              {history.all.map((run) => (
                <tr
                  key={run.id}
                  className={selectedRunId === run.id ? "is-selected" : ""}
                  onClick={() => onSelectRun(run.id)}
                >
                  <td>
                    <button
                      type="button"
                      className="run-select-button"
                      aria-pressed={selectedRunId === run.id}
                      aria-label={`Lauf ${run.number} auswählen`}
                      onClick={(event) => {
                        event.stopPropagation();
                        onSelectRun(run.id);
                      }}
                    >
                      {run.number}
                    </button>
                  </td><td>{run.date}</td><td>{run.time}</td>
                  <td>{run.actual}</td><td className="penalty-cell">{run.added}</td>
                  <td className="total-cell">{run.total}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!history.all.length && <EmptyState>Noch keine gespeicherten Läufe</EmptyState>}
        </div>
        <div className="curation-bar">
          <div>
            <span>AUSGEWÄHLTEN ZUSCHLAG</span>
            <div className="curation-buttons">
              {[-10000, -5000, 5000, 10000].map((delta) => (
                <Button
                  key={delta}
                  variant="secondary"
                  disabled={!selectedRunId || pending.confirmation}
                  onClick={() => onPrepare("adjust", selectedRunId, delta)}
                >
                  {delta > 0 ? <Plus size={12} /> : <Minus size={12} />}
                  {Math.abs(delta / 1000)} SEK
                </Button>
              ))}
            </div>
          </div>
          <Button
            variant="danger"
            className="delete-control"
            disabled={!selectedRunId || pending.confirmation}
            onClick={() => onPrepare("delete", selectedRunId)}
          >
            <Trash2 size={15} />LAUF LÖSCHEN
          </Button>
        </div>
        <div className="settings-feedback" role="status" aria-live="polite">{feedback}</div>
      </section>
    </div>
  );
}

function ConfirmationModal({ confirmation, busy, onCancel, onConfirm }) {
  const dialogRef = useRef(null);
  useDialogFocus(Boolean(confirmation), dialogRef);
  if (!confirmation) return null;
  const destructive = (
    confirmation.confirm_label?.includes("LÖSCHEN")
    || confirmation.confirm_label?.includes("HERUNTERFAHREN")
  );
  return (
    <div className="modal-backdrop confirmation-layer" role="presentation">
      <section
        ref={dialogRef}
        className="confirmation-modal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirmation-title"
        aria-describedby="confirmation-message"
      >
        <div className={`confirmation-icon ${destructive ? "is-danger" : ""}`}>
          {destructive ? <RotateCcw size={23} /> : <ShieldCheck size={23} />}
        </div>
        <span>BESTÄTIGUNG ERFORDERLICH</span>
        <h2 id="confirmation-title">{confirmation.title}</h2>
        <p id="confirmation-message">{confirmation.message}</p>
        {confirmation.lines?.length > 0 && (
          <div className="confirmation-details">
            {confirmation.lines.map((line) => <span key={line}>{line}</span>)}
          </div>
        )}
        {confirmation.warning && <div className="confirmation-warning">{confirmation.warning}</div>}
        <div className="confirmation-actions">
          <Button variant="secondary" onClick={onCancel}>ABBRECHEN</Button>
          <Button
            variant={destructive ? "danger" : "primary"}
            className={destructive ? "is-danger" : "is-confirm"}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "BITTE WARTEN …" : confirmation.confirm_label}
          </Button>
        </div>
      </section>
    </div>
  );
}

function App() {
  const {
    state,
    history,
    system,
    connection,
    chartDays,
    setChartDays,
    sendAction,
    sendAudioRequest,
    downloadExport,
    prepareConfirmation,
    confirmPrepared,
    pending,
  } = useTaktServer();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [confirmation, setConfirmation] = useState(null);
  const [confirmationBusy, setConfirmationBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [exportBusy, setExportBusy] = useState("");
  const [toast, setToast] = useState(null);
  const [now, setNow] = useState(new Date());
  const lastSignalRevision = useRef(state.signal_revision);
  const screenAwake = useScreenAwake();

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (state.state === "running" || state.start_sequence?.active) {
      queueMicrotask(() => setSettingsOpen(false));
    }
  }, [state.start_sequence?.active, state.state]);

  useEffect(() => {
    if (
      system.mock_buzzer
      && lastSignalRevision.current !== state.signal_revision
      && state.signal
    ) {
      setToast(`SUMMER-MOCK · ${state.signal.toUpperCase()}`);
      browserBeep();
      const timer = setTimeout(() => setToast(null), 900);
      lastSignalRevision.current = state.signal_revision;
      return () => clearTimeout(timer);
    }
    lastSignalRevision.current = state.signal_revision;
  }, [state.signal, state.signal_revision, system.mock_buzzer]);

  const dateText = new Intl.DateTimeFormat("de-DE", {
    weekday: "short",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(now).replace(",", " ·");

  const handleAction = async (action) => {
    try {
      await sendAction(action);
    } catch (error) {
      setToast(error.message);
      setTimeout(() => setToast(null), 3000);
    }
  };

  const handleExport = async (format) => {
    setExportBusy(format);
    setFeedback("Export wird vorbereitet …");
    try {
      const filename = await downloadExport(format);
      setFeedback(`${filename} wurde zum Herunterladen bereitgestellt.`);
    } catch (error) {
      setFeedback(error.message);
    } finally {
      setExportBusy("");
    }
  };

  const handlePrepare = async (operation, runId = null, deltaMs = 0) => {
    try {
      const prepared = await prepareConfirmation(operation, runId, deltaMs);
      setConfirmation(prepared);
    } catch (error) {
      setToast(error.message);
      setTimeout(() => setToast(null), 3000);
    }
  };

  const handleConfirm = async () => {
    if (!confirmation) return;
    setConfirmationBusy(true);
    if (confirmation.operation === "shutdown") {
      setFeedback("Herunterfahren wird angefordert …");
    }
    try {
      const result = await confirmPrepared(confirmation.confirmation_id, confirmation.operation);
      setFeedback(result.message);
      setToast(result.message);
      setConfirmation(null);
      setTimeout(() => setToast(null), 2500);
    } catch (error) {
      if (confirmation.operation === "shutdown") setFeedback("");
      setToast(error.message);
      setTimeout(() => setToast(null), 3000);
    } finally {
      setConfirmationBusy(false);
    }
  };

  useEffect(() => {
    const keyHandler = (event) => {
      const tag = event.target?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea") return;
      if (confirmation) {
        if (event.key === "Escape") setConfirmation(null);
        return;
      }
      if (settingsOpen) {
        if (event.key === "Escape") setSettingsOpen(false);
        if (event.key === "F11") {
          event.preventDefault();
          if (document.fullscreenElement) document.exitFullscreen();
          else document.documentElement.requestFullscreen();
        }
        return;
      }
      if (event.key === " " && !event.target.closest("button")) {
        event.preventDefault();
        handleAction("primary");
      } else if (event.key === "Enter" && state.state === "stopped") {
        handleAction("save");
      } else if (event.key === "Enter" && state.state === "discard_confirmation") {
        handleAction("confirm_discard");
      } else if (event.key.toLowerCase() === "r" && state.state === "stopped") {
        handleAction("request_discard");
      } else if (event.key === "Escape" && state.state === "discard_confirmation") {
        handleAction("cancel_discard");
      } else if (event.key === "Escape" && state.state === "stopped") {
        handleAction("request_discard");
      } else if (event.key === "5" && state.state === "stopped") {
        handleAction(event.ctrlKey ? "subtract_5" : "add_5");
      } else if (event.key === "0" && state.state === "stopped") {
        handleAction(event.ctrlKey ? "subtract_10" : "add_10");
      } else if (event.key === "F11") {
        event.preventDefault();
        if (document.fullscreenElement) document.exitFullscreen();
        else document.documentElement.requestFullscreen();
      }
    };
    window.addEventListener("keydown", keyHandler);
    return () => window.removeEventListener("keydown", keyHandler);
  });

  return (
    <div className={`takt-app state-${state.state}`}>
      <Header
        connection={connection}
        dateText={dateText}
        onOpenSettings={() => setSettingsOpen(true)}
        settingsDisabled={state.state === "running" || state.start_sequence?.active || pending.action}
      />
      <main className="control-grid">
        <TimerPanel state={state} screenAwake={screenAwake} onAction={handleAction} pending={pending.action} />
        <TodayPanel history={history} />
        <BestPanel history={history} />
        <ChartPanel history={history} chartDays={chartDays} onPeriodChange={setChartDays} />
      </main>
      <Footer
        state={state}
        system={system}
        screenAwake={screenAwake}
        onMockPress={() => handleAction("mock_primary")}
        pending={pending.action}
      />
      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        history={history}
        system={system}
        selectedRunId={selectedRunId}
        onSelectRun={setSelectedRunId}
        onPrepare={handlePrepare}
        onAudioRequest={sendAudioRequest}
        onExport={handleExport}
        exportBusy={exportBusy}
        pending={pending}
        blocked={Boolean(confirmation)}
        feedback={feedback}
      />
      <ConfirmationModal
        confirmation={confirmation}
        busy={confirmationBusy || pending.confirmation}
        onCancel={() => setConfirmation(null)}
        onConfirm={handleConfirm}
      />
      {toast && <div className="toast-message" role="status" aria-live="polite">{toast}</div>}
    </div>
  );
}

export default App;
