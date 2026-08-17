export function timeAgo(value) {
  if (!value) return "never";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value));
}

export function bytes(value) {
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


export function formatStopwatch(milliseconds) {
  if (milliseconds == null) return "—";
  const totalHundredths = Math.round(milliseconds / 10);
  const hundredths = totalHundredths % 100;
  const totalSeconds = Math.floor(totalHundredths / 100);
  const seconds = totalSeconds % 60;
  const minutes = Math.floor(totalSeconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(hundredths).padStart(2, "0")}`;
}

export function formatDateTime(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("de-DE", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function formatDate(value) {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return value;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return new Intl.DateTimeFormat("de-DE", { dateStyle: "medium" }).format(date);
}

const MIRROR_STATE_LABELS_DE = {
  missing: "kein Spiegel",
  offline: "offline",
  pending: "wird aktualisiert",
  fresh: "aktuell",
};
export function mirrorStateLabel(state) {
  return MIRROR_STATE_LABELS_DE[state] || state;
}

const PORTAL_ERROR_MESSAGES_DE = {
  "Device does not exist.": "Dieses Gerät existiert nicht.",
  "Release does not exist.": "Diese Version existiert nicht.",
  "Device access has been revoked.": "Der Gerätezugriff wurde widerrufen.",
  "Device must be online to queue a job.": "Das Gerät muss online sein.",
  "Another disruptive operation is already queued for this device.":
    "Für dieses Gerät ist bereits ein anderer Vorgang eingeplant.",
};
export function translatePortalError(message) {
  return PORTAL_ERROR_MESSAGES_DE[message] || message;
}

// A 401 means the session cookie the browser sent was rejected. Treating that
// as source-of-truth by re-checking /api/session (rather than showing the raw
// backend error) lets a genuinely dead session redirect to the login screen
// while a session that is actually still valid keeps the dashboard up.
export function isSessionExpired(failure) {
  return failure?.status === 401;
}

export function insecureRemoteHttp(value) {
  try {
    const parsed = new URL(value);
    const hostname = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
    const loopback = hostname === "localhost" || hostname.endsWith(".localhost")
      || hostname === "::1" || /^127\./.test(hostname);
    return parsed.protocol === "http:" && !loopback;
  } catch {
    return false;
  }
}
