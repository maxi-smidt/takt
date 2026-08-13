import assert from "node:assert/strict";
import test from "node:test";

import { compareVersions, preferredReleaseId } from "./releaseSelection.js";

test("compares dotted version segments numerically", () => {
  assert.equal(compareVersions("0.2.0", "0.10.0"), -1);
  assert.equal(compareVersions("0.10.0", "0.2.0"), 1);
  assert.equal(compareVersions("0.2.0", "0.2.0"), 0);
  assert.equal(compareVersions("1.0", "1.0.0"), 0);
});

test("prefers the newest bundled release over uploads", () => {
  const releases = [
    { id: "upload-newer", version: "0.3.0", source: "upload" },
    { id: "bundled-older", version: "0.1.0", source: "bundled" },
    { id: "bundled-newest", version: "0.2.5", source: "bundled" },
  ];
  assert.equal(preferredReleaseId(releases), "bundled-newest");
});

test("falls back to the first release when nothing is bundled", () => {
  const releases = [
    { id: "upload-a", version: "0.3.0", source: "upload" },
    { id: "upload-b", version: "0.1.0", source: "upload" },
  ];
  assert.equal(preferredReleaseId(releases), "upload-a");
});

test("returns an empty id for an empty release list", () => {
  assert.equal(preferredReleaseId([]), "");
});
