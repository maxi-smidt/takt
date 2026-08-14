import { expect, it } from "vitest";
import { parseDeploymentEvent, parsePiEvent, parseState } from "./contracts";

const state = {
  state: "ready",
  state_label: "BEREIT",
  actual_ms: 1,
  actual: "00:00.00",
  added_ms: 0,
  added: "+00:00.00",
  total_ms: 1,
  total: "00:00.00",
  error: null,
  hardware: { label: "ok", available: true },
  history_revision: 1,
  signal_revision: 2,
  signal: null,
  start_sequence: { active: false, phase: null, remaining_ms: 0, error: null },
  maintenance: { held: false, reason: null, expires_in_seconds: null },
};

it("validates consumed timer fields and tolerates additive fields", () => {
  expect(parseState({ ...state, future_field: true }).state).toBe("ready");
});

it("rejects malformed websocket payloads", () => {
  expect(() =>
    parsePiEvent({ type: "state", data: { ...state, actual_ms: "bad" } }),
  ).toThrow(/actual_ms/);
  expect(() => parsePiEvent({ type: "unknown", data: {} })).toThrow(
    /Unsupported event type/,
  );
});

it("accepts deployment events without a current deployment", () => {
  expect(
    parseDeploymentEvent({
      id: 1,
      level: "info",
      stage: "discovery",
      message: "Waiting for a device.",
      deployment: null,
    }),
  ).not.toHaveProperty("deployment");
});
