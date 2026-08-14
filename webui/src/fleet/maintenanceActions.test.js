import assert from "node:assert/strict";
import test from "node:test";

import {
  MAINTENANCE_ACTIONS,
  actionAvailability,
  healthTone,
  requiresOverride,
} from "./maintenanceActions.js";

const online = (capabilities = [], extra = {}) => ({
  online: true,
  status: { capabilities, health: { state: "ready" }, ...extra },
});

test("capability gating disables only the unsupported action", () => {
  const device = online(["leased-jobs"]);
  assert.equal(actionAvailability("restart_takt", device).enabled, true);
  assert.equal(actionAvailability("reboot_device", device).enabled, false);
  assert.match(actionAvailability("reboot_device", device).reason, /power-control-v1/);
  assert.match(actionAvailability("reboot_device", device).reason, /installer once over SSH/);
});

test("a capable agent enables the whole panel", () => {
  const device = online([
    "leased-jobs",
    "service-control-v1",
    "power-control-v1",
    "diagnostics-v1",
    "health-checks-v1",
  ]);
  for (const action of Object.keys(MAINTENANCE_ACTIONS)) {
    assert.equal(actionAvailability(action, device).enabled, true, action);
  }
});

test("offline and revoked devices disable every action", () => {
  const capabilities = ["leased-jobs", "power-control-v1"];
  const offline = { online: false, status: { capabilities } };
  const revoked = { online: true, revoked_at: "2026-08-13", status: { capabilities } };
  assert.match(actionAvailability("reboot_device", offline).reason, /offline/);
  assert.match(actionAvailability("restart_takt", revoked).reason, /revoked/);
});

test("a stuck update blocks maintenance until it is resolved", () => {
  const device = online(["leased-jobs"], { update_recovery: { stuck: true } });
  assert.match(actionAvailability("restart_takt", device).reason, /update recovery/);
});

test("override is required only while a run is in progress", () => {
  const ready = online(["leased-jobs", "power-control-v1"]);
  assert.equal(requiresOverride("reboot_device", ready), false);

  const running = {
    online: true,
    status: { capabilities: ["power-control-v1"], health: { state: "running" } },
  };
  assert.equal(requiresOverride("reboot_device", running), true);
  assert.equal(requiresOverride("reboot_device", { online: false, status: { health: { state: "unreachable" } } }), false);
  assert.equal(requiresOverride("reboot_device", { online: false, status: { health: { state: "stopped" } } }), false);
  // A read-only action is never overridable, however busy the timer is.
  assert.equal(requiresOverride("run_health_checks", running), false);
});

test("health tone reflects the worst check", () => {
  assert.equal(healthTone(undefined), "unknown");
  assert.equal(healthTone({ summary: { fail: 0, warn: 0 } }), "ok");
  assert.equal(healthTone({ summary: { fail: 0, warn: 2 } }), "warn");
  assert.equal(healthTone({ summary: { fail: 1, warn: 2 } }), "fail");
});

test("unknown actions are refused rather than rendered", () => {
  assert.equal(actionAvailability("rm_rf", online(["leased-jobs"])).enabled, false);
});
