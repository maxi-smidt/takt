import { expect, it } from "vitest";
import { compareVersions, preferredReleaseId } from "./releaseSelection";

it("compares dotted version segments numerically", () => {
  expect(compareVersions("0.2.0", "0.10.0")).toBe(-1);
  expect(compareVersions("0.10.0", "0.2.0")).toBe(1);
  expect(compareVersions("0.2.0", "0.2.0")).toBe(0);
  expect(compareVersions("1.0", "1.0.0")).toBe(0);
});

it("prefers the newest bundled release over uploads", () => {
  const releases = [
    { id: "upload-newer", version: "0.3.0", source: "upload" },
    { id: "bundled-older", version: "0.1.0", source: "bundled" },
    { id: "bundled-newest", version: "0.2.5", source: "bundled" },
  ];
  expect(preferredReleaseId(releases)).toBe("bundled-newest");
});

it("falls back to the first release when nothing is bundled", () => {
  const releases = [
    { id: "upload-a", version: "0.3.0", source: "upload" },
    { id: "upload-b", version: "0.1.0", source: "upload" },
  ];
  expect(preferredReleaseId(releases)).toBe("upload-a");
});

it("returns an empty id for an empty release list", () => {
  expect(preferredReleaseId([])).toBe("");
});
it("ranks a final release above its own prerelease", () => {
  expect(compareVersions("1.0.0-rc.1", "1.0.0")).toBe(-1);
  expect(compareVersions("1.0.0", "1.0.0-rc.1")).toBe(1);
});
it("compares prerelease identifiers left to right per semver precedence", () => {
  expect(compareVersions("1.0.0-alpha", "1.0.0-alpha.1")).toBe(-1);
  expect(compareVersions("1.0.0-alpha.1", "1.0.0-alpha.beta")).toBe(-1);
  expect(compareVersions("1.0.0-alpha.beta", "1.0.0-beta")).toBe(-1);
  expect(compareVersions("1.0.0-rc.1", "1.0.0-rc.1")).toBe(0);
});
it("preselects the final release over a same-core prerelease", () => {
  const releases = [
    { id: "bundled-rc", version: "1.0.0-rc.1", source: "bundled" },
    { id: "bundled-final", version: "1.0.0", source: "bundled" },
  ];
  expect(preferredReleaseId(releases)).toBe("bundled-final");
});
