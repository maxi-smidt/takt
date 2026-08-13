import assert from "node:assert/strict";
import test from "node:test";

import { deploymentTargetError, hostnameChangeError } from "./deploymentValidation.js";

test("matches the registry hostname and IPv6 target rules", () => {
  assert.equal(deploymentTargetError("pi.local"), "");
  assert.equal(deploymentTargetError("2001:db8::1"), "");
  assert.match(deploymentTargetError("pi_local"), /invalid/);
  assert.match(deploymentTargetError("a".repeat(254)), /invalid/);
});

test("requires confirmation for an optional hostname change", () => {
  assert.equal(hostnameChangeError("", false), "");
  assert.match(hostnameChangeError("takt_01", true), /invalid/);
  assert.match(hostnameChangeError("takt-01", false), /Confirm/);
  assert.equal(hostnameChangeError("takt-01", true), "");
});
