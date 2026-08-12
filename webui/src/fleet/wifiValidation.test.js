import assert from "node:assert/strict";
import test from "node:test";

import { wifiNetworkError } from "./wifiValidation.js";

const validPassword = "fleet-secret-123";

test("validates SSIDs by UTF-8 byte length", () => {
  assert.equal(wifiNetworkError("ä".repeat(16), validPassword), "");
  assert.match(wifiNetworkError("ä".repeat(17), validPassword), /32 UTF-8 bytes/);
  assert.match(wifiNetworkError("Timing\nHall", validPassword), /without controls/);
});

test("matches the server's WPA passphrase and raw PSK rules", () => {
  assert.equal(wifiNetworkError("Timing Hall", "a".repeat(63)), "");
  assert.equal(wifiNetworkError("Timing Hall", "a0".repeat(32)), "");
  assert.match(wifiNetworkError("Timing Hall", "g".repeat(64)), /64 hexadecimal/);
  assert.match(wifiNetworkError("Timing Hall", "pässword"), /printable ASCII/);
});
