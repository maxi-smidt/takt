// Mirrors src/takt/fleet_actions.py. The registry is authoritative and refuses
// anything this table gets wrong; this copy only decides how buttons render.
export const MAINTENANCE_ACTIONS = {
  start_takt: {
    label: "START",
    group: "service",
    capability: "service-control-v1",
    confirm: "start the TAKT service",
  },
  stop_takt: {
    label: "STOP",
    group: "service",
    capability: "service-control-v1",
    confirm: "stop the TAKT service",
    destructive: true,
    overridable: true,
  },
  restart_takt: {
    label: "RESTART",
    group: "service",
    capability: "leased-jobs",
    confirm: "restart the TAKT service",
    overridable: true,
  },
  reboot_device: {
    label: "REBOOT",
    group: "power",
    capability: "power-control-v1",
    confirm: "reboot this Raspberry Pi",
    destructive: true,
    overridable: true,
    aftermath: "The device goes offline and should reconnect within about a minute.",
  },
  shutdown_device: {
    label: "SHUT DOWN",
    group: "power",
    capability: "power-control-v1",
    confirm: "shut down this Raspberry Pi",
    destructive: true,
    overridable: true,
    aftermath: "The device leaves the fleet until someone powers it on by hand.",
  },
  run_health_checks: {
    label: "HEALTH CHECKS",
    group: "diagnose",
    capability: "health-checks-v1",
    confirm: "run health checks",
  },
  collect_diagnostics: {
    label: "DIAGNOSTICS",
    group: "diagnose",
    capability: "diagnostics-v1",
    confirm: "collect a redacted diagnostics bundle",
  },
};

export const ACTION_GROUPS = [
  { id: "service", label: "SERVICE" },
  { id: "power", label: "POWER" },
  { id: "diagnose", label: "DIAGNOSE" },
];

// Capabilities every agent that can heartbeat has always had, so they are never
// gated on the reported capability list.
const BASELINE_CAPABILITY = "leased-jobs";

export function actionAvailability(action, device) {
  const definition = MAINTENANCE_ACTIONS[action];
  if (!definition) return { enabled: false, reason: "Unknown action." };
  if (device.revoked_at) return { enabled: false, reason: "This device credential was revoked." };
  if (!device.online) return { enabled: false, reason: "This device is offline." };
  if (device.status?.update_recovery?.stuck) {
    return { enabled: false, reason: "Finish the pending update recovery first." };
  }
  if (definition.capability === BASELINE_CAPABILITY) return { enabled: true, reason: "" };
  const capabilities = device.status?.capabilities;
  if (!capabilities?.includes(definition.capability)) {
    return {
      enabled: false,
      reason:
        `This Pi's Fleet agent does not offer ${definition.capability} yet. ` +
        "Run the Pi installer once over SSH to install the maintenance helper.",
    };
  }
  return { enabled: true, reason: "" };
}

// The timer must be idle, or the operator must explicitly accept losing the run.
export function requiresOverride(action, device) {
  const definition = MAINTENANCE_ACTIONS[action];
  if (!definition?.overridable) return false;
  const state = device.status?.health?.state;
  return state === "running";
}

export function healthTone(healthChecks) {
  const summary = healthChecks?.summary;
  if (!summary) return "unknown";
  if (summary.fail > 0) return "fail";
  if (summary.warn > 0) return "warn";
  return "ok";
}
