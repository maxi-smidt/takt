function compareCore(partsA, partsB) {
  const length = Math.max(partsA.length, partsB.length);
  for (let index = 0; index < length; index += 1) {
    const partA = partsA[index] ?? "";
    const partB = partsB[index] ?? "";
    const numberA = Number(partA);
    const numberB = Number(partB);
    if (Number.isNaN(numberA) || Number.isNaN(numberB)) {
      if (partA !== partB) return partA < partB ? -1 : 1;
    } else if (numberA !== numberB) {
      return numberA < numberB ? -1 : 1;
    }
  }
  return 0;
}

// Per semver precedence rules: numeric identifiers compare numerically,
// alphanumeric identifiers compare lexically, and a numeric identifier
// always sorts below an alphanumeric one.
function compareIdentifiers(a, b) {
  const numberA = /^\d+$/.test(a) ? Number(a) : null;
  const numberB = /^\d+$/.test(b) ? Number(b) : null;
  if (numberA !== null && numberB !== null) {
    if (numberA !== numberB) return numberA < numberB ? -1 : 1;
    return 0;
  }
  if (numberA !== null) return -1;
  if (numberB !== null) return 1;
  if (a === b) return 0;
  return a < b ? -1 : 1;
}

// A version without a prerelease outranks the same core version with one;
// between two prereleases, identifiers compare left to right and a shorter
// list loses ties on a shared prefix (e.g. "-alpha" < "-alpha.1").
function comparePrerelease(prereleaseA, prereleaseB) {
  if (!prereleaseA.length && !prereleaseB.length) return 0;
  if (!prereleaseA.length) return 1;
  if (!prereleaseB.length) return -1;
  const length = Math.max(prereleaseA.length, prereleaseB.length);
  for (let index = 0; index < length; index += 1) {
    if (index >= prereleaseA.length) return -1;
    if (index >= prereleaseB.length) return 1;
    const comparison = compareIdentifiers(prereleaseA[index], prereleaseB[index]);
    if (comparison !== 0) return comparison;
  }
  return 0;
}

function splitVersion(value) {
  const [core, ...prereleaseParts] = String(value).split("-");
  return {
    core: core.split("."),
    prerelease: prereleaseParts.length ? prereleaseParts.join("-").split(".") : [],
  };
}

export function compareVersions(a, b) {
  const versionA = splitVersion(a);
  const versionB = splitVersion(b);
  const coreComparison = compareCore(versionA.core, versionB.core);
  if (coreComparison !== 0) return coreComparison;
  return comparePrerelease(versionA.prerelease, versionB.prerelease);
}

// Prefer the newest CI-bundled, checksum-verified release; fall back to the
// most recently added release (releases are ordered created_at DESC by the
// registry) so an operator's manual upload still works when nothing is
// bundled yet.
export function preferredReleaseId(releases) {
  const verified = releases.filter((release) => release.source === "bundled");
  if (!verified.length) return releases[0]?.id || "";
  const newest = verified.reduce((best, release) =>
    compareVersions(release.version, best.version) > 0 ? release : best
  );
  return newest.id;
}
