import { expect, it } from "vitest";
import { wifiNetworkError } from "./wifiValidation";

const validPassword = "fleet-secret-123";

it("validates SSIDs by UTF-8 byte length", () => {
  expect(wifiNetworkError("ä".repeat(16), validPassword)).toBe("");
  expect(wifiNetworkError("ä".repeat(17), validPassword)).toMatch(
    /32 UTF-8 bytes/,
  );
  expect(wifiNetworkError("Timing\nHall", validPassword)).toMatch(
    /without controls/,
  );
});

it("matches the server's WPA passphrase and raw PSK rules", () => {
  expect(wifiNetworkError("Timing Hall", "a".repeat(63))).toBe("");
  expect(wifiNetworkError("Timing Hall", "a0".repeat(32))).toBe("");
  expect(wifiNetworkError("Timing Hall", "g".repeat(64))).toMatch(
    /64 hexadecimal/,
  );
  expect(wifiNetworkError("Timing Hall", "pässword")).toMatch(
    /printable ASCII/,
  );
});
