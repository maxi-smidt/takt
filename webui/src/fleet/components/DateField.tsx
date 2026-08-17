import { useEffect, useRef, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import {
  MONTH_LABELS_DE,
  WEEKDAY_LABELS_DE,
  buildCalendarWeeks,
  formatDisplayDate,
  formatIsoDate,
  parseDisplayDate,
} from "../dateInput";

interface DateFieldProps {
  label: string;
  value: string; // ISO yyyy-mm-dd, or "" when unset
  onChange: (value: string) => void;
}

export function DateField({ label, value, onChange }: DateFieldProps) {
  const [text, setText] = useState(() => formatDisplayDate(value));
  const [syncedValue, setSyncedValue] = useState(value);
  const [open, setOpen] = useState(false);
  const [invalid, setInvalid] = useState(false);
  const [viewDate, setViewDate] = useState(() => {
    const parsed = value ? new Date(value) : new Date();
    return new Date(parsed.getFullYear(), parsed.getMonth(), 1);
  });
  const containerRef = useRef<HTMLDivElement>(null);

  // The typed text only needs to resync when `value` changes from outside
  // (e.g. a calendar pick or a timeframe preset) — deriving it during render
  // avoids the extra pass a useEffect-based sync would cause.
  if (value !== syncedValue) {
    setSyncedValue(value);
    setText(formatDisplayDate(value));
    setInvalid(false);
  }

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  const commitText = (raw: string) => {
    if (!raw.trim()) {
      setInvalid(false);
      onChange("");
      return;
    }
    const parsed = parseDisplayDate(raw);
    if (!parsed) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    onChange(parsed);
  };

  const pickDay = (day: Date) => {
    onChange(formatIsoDate(day));
    setOpen(false);
  };

  const weeks = buildCalendarWeeks(viewDate.getFullYear(), viewDate.getMonth());
  const selectedIso = value || null;

  return (
    <div className="date-field" ref={containerRef}>
      <label className="field-label">
        {label}
        <div className={"date-field-input" + (invalid ? " is-invalid" : "")}>
          <input
            type="text"
            inputMode="numeric"
            placeholder="TT.MM.JJJJ"
            value={text}
            onChange={(event) => setText(event.target.value)}
            onBlur={(event) => commitText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") commitText(event.currentTarget.value);
            }}
          />
          <button
            type="button"
            className="icon-button date-field-toggle"
            aria-label="Kalender öffnen"
            onClick={() => {
              if (!open && value) setViewDate(new Date(new Date(value).getFullYear(), new Date(value).getMonth(), 1));
              setOpen((current) => !current);
            }}
          >
            <CalendarDays size={16} />
          </button>
        </div>
      </label>
      {open && (
        <div className="date-field-popup" role="dialog" aria-label={`Kalender – ${label}`}>
          <div className="date-field-popup-nav">
            <button
              type="button"
              aria-label="Vorheriger Monat"
              onClick={() => setViewDate(new Date(viewDate.getFullYear(), viewDate.getMonth() - 1, 1))}
            >
              <ChevronLeft size={16} />
            </button>
            <strong>{MONTH_LABELS_DE[viewDate.getMonth()]} {viewDate.getFullYear()}</strong>
            <button
              type="button"
              aria-label="Nächster Monat"
              onClick={() => setViewDate(new Date(viewDate.getFullYear(), viewDate.getMonth() + 1, 1))}
            >
              <ChevronRight size={16} />
            </button>
          </div>
          <div className="date-field-weekdays">
            {WEEKDAY_LABELS_DE.map((weekday) => <span key={weekday}>{weekday}</span>)}
          </div>
          <div className="date-field-grid">
            {weeks.flat().map((day) => {
              const iso = formatIsoDate(day);
              const outsideMonth = day.getMonth() !== viewDate.getMonth();
              return (
                <button
                  type="button"
                  key={iso}
                  className={
                    "date-field-day"
                    + (outsideMonth ? " is-outside" : "")
                    + (iso === selectedIso ? " is-selected" : "")
                  }
                  onClick={() => pickDay(day)}
                >
                  {day.getDate()}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
