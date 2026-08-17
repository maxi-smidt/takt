// Shared helpers for the German dd.mm.yyyy date picker used across the portal.
// Internal/API values stay ISO (yyyy-mm-dd); only display and typing use dd.mm.yyyy.

export const WEEKDAY_LABELS_DE = ["MO", "DI", "MI", "DO", "FR", "SA", "SO"];
export const MONTH_LABELS_DE = [
  "Januar", "Februar", "März", "April", "Mai", "Juni",
  "Juli", "August", "September", "Oktober", "November", "Dezember",
];

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

export function formatIsoDate(date: Date): string {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

export function formatDisplayDate(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) return "";
  return `${match[3]}.${match[2]}.${match[1]}`;
}

// Parses a dd.mm.yyyy string into an ISO date, rejecting values that don't
// round-trip through the Date constructor (e.g. 31.02.2026).
export function parseDisplayDate(text: string): string | null {
  const match = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(text.trim());
  if (!match) return null;
  const day = Number(match[1]);
  const month = Number(match[2]);
  const year = Number(match[3]);
  const date = new Date(year, month - 1, day);
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) {
    return null;
  }
  return formatIsoDate(date);
}

// Monday-first weeks covering the given month, padded with adjacent-month
// days so every week has 7 entries.
export function buildCalendarWeeks(year: number, month: number): Date[][] {
  const firstOfMonth = new Date(year, month, 1);
  const lastOfMonth = new Date(year, month + 1, 0);
  const mondayOffset = (firstOfMonth.getDay() + 6) % 7;
  let cursor = new Date(year, month, 1 - mondayOffset);

  const weeks: Date[][] = [];
  while (cursor <= lastOfMonth) {
    const days: Date[] = [];
    for (let day = 0; day < 7; day += 1) {
      days.push(cursor);
      cursor = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() + 1);
    }
    weeks.push(days);
  }
  return weeks;
}
