import { ApiError } from "./httpClient";

export type JsonRecord = Record<string, unknown>;
export interface Run {
  id: number;
  number: number;
  date: string;
  date_short: string;
  time: string;
  timestamp: string;
  actual_ms: number;
  actual: string;
  added_ms: number;
  added: string;
  total_ms: number;
  total: string;
  rank?: number;
}
export interface HistoryPayload {
  today: Run[];
  today_count: number;
  best: Run[];
  chart: Run[];
  all: Run[];
  chart_days: number | null;
}
export interface TimerStatePayload {
  state: string;
  state_label: string;
  actual_ms: number;
  actual: string;
  added_ms: number;
  added: string;
  total_ms: number;
  total: string;
  error: string | null;
  hardware: { label: string; available: boolean };
  history_revision: number;
  signal_revision: number;
  signal: string | null;
  run_signal: string | null;
  sound_playing?: boolean;
  start_sequence: {
    active: boolean;
    phase: string | null;
    remaining_ms: number;
    error: string | null;
  };
  maintenance: {
    held: boolean;
    reason: string | null;
    expires_in_seconds: number | null;
  };
}
export interface AudioDevice {
  address: string;
  name: string;
  paired: boolean;
  connected: boolean;
}
export interface AudioSettings {
  enabled: boolean;
  output: string;
  delay_milliseconds: number;
  clip_duration_milliseconds: number;
  device_address: string | null;
  device_name: string | null;
  playback_available: boolean;
  bluetooth_available: boolean;
  sound: string;
  run_signals_enabled: boolean;
  devices: AudioDevice[];
  player?: string | null;
  scanning?: boolean;
}
export interface SystemPayload {
  shutdown_available: boolean;
  model: string;
  mock_button: boolean;
  mock_buzzer: boolean;
  audio: AudioSettings;
}
export interface BootstrapPayload {
  state: TimerStatePayload;
  history: HistoryPayload;
  system: SystemPayload;
}
export interface ConfirmationPayload {
  confirmation_id: string;
  operation: string;
  title: string;
  message: string;
  confirm_label: string;
  warning?: string | null;
  lines?: string[];
}
export interface ActionResponse {
  ok: boolean;
  state: TimerStatePayload;
}
export interface SystemResponse {
  ok: boolean;
  system: SystemPayload;
}
export interface ConfirmationResponse {
  message: string;
  [key: string]: unknown;
}
export type PiEvent =
  | { type: "state"; data: TimerStatePayload }
  | { type: "system"; data: SystemPayload }
  | { type: "history_changed"; revision?: number };
export interface SessionResponse {
  user?: {
    id: string;
    username: string;
    is_admin: boolean;
    must_change_password?: boolean;
  };
  authenticated: boolean;
  csrf_token?: string;
}
export interface DeviceStatus {
  [key: string]: unknown;
  health?: JsonRecord;
  update_recovery?: JsonRecord;
  capabilities?: string[];
}
export interface Device {
  id: string;
  name: string;
  hostname: string;
  online: boolean;
  status: DeviceStatus;
  app_version?: string | null;
  agent_version?: string | null;
  revoked_at?: string | null;
  run_count?: number | null;
  last_seen_at?: string | null;
  last_mirror_at?: string | null;
  mirror_size?: number | null;
}
export interface Release {
  id: string;
  version: string;
  source: string;
  installed: boolean;
  filename?: string;
  sha256?: string;
  size?: number;
  created_at?: string;
  commit_sha?: string | null;
}
export interface BundledReleaseStatus {
  status: string;
  reason?: string;
  detail?: string;
  [key: string]: unknown;
}
export interface Job {
  id: string;
  device_id: string;
  device_name: string;
  action: string;
  status: string;
  stage?: string | null;
  message?: string | null;
  progress?: number | null;
  bytes_downloaded?: number | null;
  bytes_total?: number | null;
  attempt?: number;
  updated_at?: string;
  current_version?: string | null;
  target_version?: string | null;
  device_online?: boolean;
  device_last_seen_at?: string | null;
}
export interface Deployment {
  id: string;
  status: string;
  stage?: string | null;
  message?: string | null;
  host_key_fingerprint?: string | null;
  [key: string]: unknown;
}
export interface DeploymentEvent {
  id: number;
  level: string;
  stage: string;
  message: string;
  deployment?: Deployment;
}

function object(value: unknown, label: string): JsonRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new ApiError(200, `${label} must be an object.`);
  return value as JsonRecord;
}
function text(value: unknown, field: string): string {
  if (typeof value !== "string")
    throw new ApiError(200, `${field} must be a string.`);
  return value;
}
function finite(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value))
    throw new ApiError(200, `${field} must be a number.`);
  return value;
}
function flag(value: unknown, field: string): boolean {
  if (typeof value !== "boolean")
    throw new ApiError(200, `${field} must be a boolean.`);
  return value;
}
function nullableText(value: unknown, field: string): string | null {
  if (value !== null && typeof value !== "string")
    throw new ApiError(200, `${field} must be nullable string.`);
  return value as string | null;
}
function list<T>(
  value: unknown,
  field: string,
  parse: (entry: unknown) => T,
): T[] {
  if (!Array.isArray(value))
    throw new ApiError(200, `${field} must be an array.`);
  return value.map(parse);
}
function optional<T>(
  item: JsonRecord,
  key: string,
  parse: (value: unknown) => T,
): T | undefined {
  return item[key] === undefined ? undefined : parse(item[key]);
}

export function parseRun(value: unknown): Run {
  const item = object(value, "run");
  const rank = optional(item, "rank", (entry) => finite(entry, "run.rank"));
  return {
    id: finite(item.id, "run.id"),
    number: finite(item.number, "run.number"),
    date: text(item.date, "run.date"),
    date_short: text(item.date_short, "run.date_short"),
    time: text(item.time, "run.time"),
    timestamp: text(item.timestamp, "run.timestamp"),
    actual_ms: finite(item.actual_ms, "run.actual_ms"),
    actual: text(item.actual, "run.actual"),
    added_ms: finite(item.added_ms, "run.added_ms"),
    added: text(item.added, "run.added"),
    total_ms: finite(item.total_ms, "run.total_ms"),
    total: text(item.total, "run.total"),
    ...(rank === undefined ? {} : { rank }),
  };
}
export function parseHistory(value: unknown): HistoryPayload {
  const item = object(value, "history");
  return {
    today: list(item.today, "history.today", parseRun),
    today_count: finite(item.today_count, "history.today_count"),
    best: list(item.best, "history.best", parseRun),
    chart: list(item.chart, "history.chart", parseRun),
    all: list(item.all, "history.all", parseRun),
    chart_days:
      item.chart_days === null
        ? null
        : finite(item.chart_days, "history.chart_days"),
  };
}
export function parseState(value: unknown): TimerStatePayload {
  const item = object(value, "state");
  const hardware = object(item.hardware, "state.hardware");
  const sequence = object(item.start_sequence, "state.start_sequence");
  const maintenance = object(item.maintenance, "state.maintenance");
  return {
    state: text(item.state, "state.state"),
    state_label: text(item.state_label, "state.state_label"),
    actual_ms: finite(item.actual_ms, "state.actual_ms"),
    actual: text(item.actual, "state.actual"),
    added_ms: finite(item.added_ms, "state.added_ms"),
    added: text(item.added, "state.added"),
    total_ms: finite(item.total_ms, "state.total_ms"),
    total: text(item.total, "state.total"),
    error: nullableText(item.error, "state.error"),
    hardware: {
      label: text(hardware.label, "hardware.label"),
      available: flag(hardware.available, "hardware.available"),
    },
    history_revision: finite(item.history_revision, "state.history_revision"),
    signal_revision: finite(item.signal_revision, "state.signal_revision"),
    signal: nullableText(item.signal, "state.signal"),
    run_signal:
      item.run_signal === undefined
        ? null
        : nullableText(item.run_signal, "state.run_signal"),
    ...(typeof item.sound_playing === "boolean"
      ? { sound_playing: item.sound_playing }
      : {}),
    start_sequence: {
      active: flag(sequence.active, "start_sequence.active"),
      phase: nullableText(sequence.phase, "start_sequence.phase"),
      remaining_ms: finite(
        sequence.remaining_ms,
        "start_sequence.remaining_ms",
      ),
      error: nullableText(sequence.error, "start_sequence.error"),
    },
    maintenance: {
      held: flag(maintenance.held, "maintenance.held"),
      reason: nullableText(maintenance.reason, "maintenance.reason"),
      expires_in_seconds:
        maintenance.expires_in_seconds === null
          ? null
          : finite(
              maintenance.expires_in_seconds,
              "maintenance.expires_in_seconds",
            ),
    },
  };
}
function parseAudio(value: unknown): AudioSettings {
  const item = object(value, "system.audio");
  return {
    enabled: flag(item.enabled, "audio.enabled"),
    output: text(item.output, "audio.output"),
    delay_milliseconds: finite(
      item.delay_milliseconds,
      "audio.delay_milliseconds",
    ),
    clip_duration_milliseconds: finite(
      item.clip_duration_milliseconds,
      "audio.clip_duration_milliseconds",
    ),
    device_address: nullableText(item.device_address, "audio.device_address"),
    device_name: nullableText(item.device_name, "audio.device_name"),
    playback_available: flag(
      item.playback_available,
      "audio.playback_available",
    ),
    bluetooth_available: flag(
      item.bluetooth_available,
      "audio.bluetooth_available",
    ),
    sound: text(item.sound, "audio.sound"),
    run_signals_enabled:
      item.run_signals_enabled === undefined
        ? true
        : flag(item.run_signals_enabled, "audio.run_signals_enabled"),
    devices: list(item.devices, "audio.devices", (entry) => {
      const device = object(entry, "audio device");
      return {
        address: text(device.address, "device.address"),
        name: text(device.name, "device.name"),
        paired: flag(device.paired, "device.paired"),
        connected: flag(device.connected, "device.connected"),
      };
    }),
    ...(typeof item.player === "string" || item.player === null
      ? { player: item.player }
      : {}),
    ...(typeof item.scanning === "boolean" ? { scanning: item.scanning } : {}),
  };
}
export function parseSystem(value: unknown): SystemPayload {
  const item = object(value, "system");
  return {
    shutdown_available: flag(
      item.shutdown_available,
      "system.shutdown_available",
    ),
    model: text(item.model, "system.model"),
    mock_button: flag(item.mock_button, "system.mock_button"),
    mock_buzzer: flag(item.mock_buzzer, "system.mock_buzzer"),
    audio: parseAudio(item.audio),
  };
}
export function parseBootstrap(value: unknown): BootstrapPayload {
  const item = object(value, "bootstrap");
  return {
    state: parseState(item.state),
    history: parseHistory(item.history),
    system: parseSystem(item.system),
  };
}
export function parseConfirmation(value: unknown): ConfirmationPayload {
  const item = object(value, "confirmation");
  return {
    confirmation_id: text(item.confirmation_id, "confirmation_id"),
    operation: text(item.operation, "operation"),
    title: text(item.title, "title"),
    message: text(item.message, "message"),
    confirm_label: text(item.confirm_label, "confirm_label"),
    ...(item.warning === null || typeof item.warning === "string"
      ? { warning: item.warning }
      : {}),
    ...(Array.isArray(item.lines)
      ? { lines: item.lines.map((line) => text(line, "confirmation.lines[]")) }
      : {}),
  };
}
export function parseAction(value: unknown): ActionResponse {
  const item = object(value, "action response");
  return { ok: flag(item.ok, "action.ok"), state: parseState(item.state) };
}
export function parseSystemResponse(value: unknown): SystemResponse {
  const item = object(value, "system response");
  return { ok: flag(item.ok, "system.ok"), system: parseSystem(item.system) };
}
export function parseConfirmationResponse(
  value: unknown,
): ConfirmationResponse {
  const item = object(value, "confirmation response");
  return { ...item, message: text(item.message, "confirmation.message") };
}
export function parsePiEvent(value: unknown): PiEvent {
  const item = object(value, "event");
  const type = text(item.type, "event.type");
  if (type === "state") return { type, data: parseState(item.data) };
  if (type === "system") return { type, data: parseSystem(item.data) };
  if (type === "history_changed")
    return {
      type,
      ...(typeof item.revision === "number" ? { revision: item.revision } : {}),
    };
  throw new ApiError(200, `Unsupported event type: ${type}`);
}
export function parseSession(value: unknown): SessionResponse {
  const item = object(value, "session");
  const rawUser = item.user;
  const user =
    typeof rawUser === "object" && rawUser !== null && !Array.isArray(rawUser)
      ? (rawUser as JsonRecord)
      : null;
  return {
    authenticated: flag(item.authenticated, "session.authenticated"),
    ...(user
      ? {
          user: {
            id: text(user.id, "session.user.id"),
            username: text(user.username, "session.user.username"),
            is_admin: flag(user.is_admin, "session.user.is_admin"),
            ...(typeof user.must_change_password === "boolean"
              ? { must_change_password: user.must_change_password as boolean }
              : {}),
          },
        }
      : {}),
    ...(typeof item.csrf_token === "string"
      ? { csrf_token: item.csrf_token }
      : {}),
  };
}
function parseDevice(value: unknown): Device {
  const item = object(value, "device");
  return {
    id: text(item.id, "device.id"),
    name: text(item.name, "device.name"),
    hostname: text(item.hostname, "device.hostname"),
    online: flag(item.online, "device.online"),
    status: (typeof item.status === "object" && item.status !== null
      ? item.status
      : {}) as DeviceStatus,
    ...(typeof item.app_version === "string" || item.app_version === null
      ? { app_version: item.app_version }
      : {}),
    ...(typeof item.agent_version === "string" || item.agent_version === null
      ? { agent_version: item.agent_version }
      : {}),
    ...(typeof item.revoked_at === "string" || item.revoked_at === null
      ? { revoked_at: item.revoked_at }
      : {}),
    ...(typeof item.run_count === "number" || item.run_count === null
      ? { run_count: item.run_count }
      : {}),
    ...(typeof item.last_seen_at === "string" || item.last_seen_at === null
      ? { last_seen_at: item.last_seen_at }
      : {}),
    ...(typeof item.last_mirror_at === "string" || item.last_mirror_at === null
      ? { last_mirror_at: item.last_mirror_at }
      : {}),
    ...(typeof item.mirror_size === "number" || item.mirror_size === null
      ? { mirror_size: item.mirror_size }
      : {}),
  };
}
function parseRelease(value: unknown): Release {
  const item = object(value, "release");
  return {
    id: text(item.id, "release.id"),
    version: text(item.version, "release.version"),
    source: text(item.source, "release.source"),
    installed: flag(item.installed, "release.installed"),
    ...(typeof item.filename === "string" ? { filename: item.filename } : {}),
    ...(typeof item.sha256 === "string" ? { sha256: item.sha256 } : {}),
    ...(typeof item.size === "number" ? { size: item.size } : {}),
    ...(typeof item.created_at === "string"
      ? { created_at: item.created_at }
      : {}),
    ...(typeof item.commit_sha === "string" || item.commit_sha === null
      ? { commit_sha: item.commit_sha }
      : {}),
  };
}
function parseJob(value: unknown): Job {
  const item = object(value, "job");
  return {
    id: text(item.id, "job.id"),
    device_id: text(item.device_id, "job.device_id"),
    device_name: text(item.device_name, "job.device_name"),
    action: text(item.action, "job.action"),
    status: text(item.status, "job.status"),
    ...(typeof item.stage === "string" || item.stage === null
      ? { stage: item.stage }
      : {}),
    ...(typeof item.message === "string" || item.message === null
      ? { message: item.message }
      : {}),
    ...(typeof item.progress === "number" || item.progress === null
      ? { progress: item.progress }
      : {}),
    ...(typeof item.bytes_downloaded === "number" ||
    item.bytes_downloaded === null
      ? { bytes_downloaded: item.bytes_downloaded }
      : {}),
    ...(typeof item.bytes_total === "number" || item.bytes_total === null
      ? { bytes_total: item.bytes_total }
      : {}),
    ...(typeof item.attempt === "number" ? { attempt: item.attempt } : {}),
    ...(typeof item.updated_at === "string"
      ? { updated_at: item.updated_at }
      : {}),
    ...(typeof item.current_version === "string" ||
    item.current_version === null
      ? { current_version: item.current_version }
      : {}),
    ...(typeof item.target_version === "string" || item.target_version === null
      ? { target_version: item.target_version }
      : {}),
  };
}
function parseDeployment(value: unknown): Deployment {
  const item = object(value, "deployment");
  return {
    id: text(item.id, "deployment.id"),
    status: text(item.status, "deployment.status"),
    ...(typeof item.stage === "string" || item.stage === null
      ? { stage: item.stage }
      : {}),
    ...(typeof item.message === "string" || item.message === null
      ? { message: item.message }
      : {}),
    ...(typeof item.host_key_fingerprint === "string" ||
    item.host_key_fingerprint === null
      ? { host_key_fingerprint: item.host_key_fingerprint }
      : {}),
  };
}
export function parseDevices(value: unknown): Device[] {
  return list(
    object(value, "devices response").devices,
    "devices",
    parseDevice,
  );
}
export function parseReleases(value: unknown): {
  releases: Release[];
  bundled_release: BundledReleaseStatus | null;
} {
  const item = object(value, "releases response");
  const bundled = item.bundled_release;
  return {
    releases: list(item.releases, "releases", parseRelease),
    bundled_release:
      bundled === null || bundled === undefined
        ? null
        : (object(bundled, "bundled_release") as BundledReleaseStatus),
  };
}
export function parseJobs(value: unknown): Job[] {
  return list(object(value, "jobs response").jobs, "jobs", parseJob);
}
export function parseDeploymentResponse(value: unknown): {
  deployment: Deployment;
} {
  return {
    deployment: parseDeployment(
      object(value, "deployment response").deployment,
    ),
  };
}
export function parseDeploymentEvent(value: unknown): DeploymentEvent {
  const item = object(value, "deployment event");
  return {
    id: finite(item.id, "event.id"),
    level: text(item.level, "event.level"),
    stage: text(item.stage, "event.stage"),
    message: text(item.message, "event.message"),
    ...(item.deployment === undefined || item.deployment === null
      ? {}
      : { deployment: parseDeployment(item.deployment) }),
  };
}
