import { expect, it } from "vitest";
import {
  deploymentTargetError,
  hostnameChangeError,
} from "./deploymentValidation";

it("matches the registry hostname and IPv6 target rules", () => {
  expect(deploymentTargetError("pi.local")).toBe("");
  expect(deploymentTargetError("2001:db8::1")).toBe("");
  expect(deploymentTargetError("pi_local")).toMatch(/invalid/);
  expect(deploymentTargetError("a".repeat(254))).toMatch(/invalid/);
});

it("requires confirmation for an optional hostname change", () => {
  expect(hostnameChangeError("", false)).toBe("");
  expect(hostnameChangeError("takt_01", true)).toMatch(/invalid/);
  expect(hostnameChangeError("takt-01", false)).toMatch(/Confirm/);
  expect(hostnameChangeError("takt-01", true)).toBe("");
});
