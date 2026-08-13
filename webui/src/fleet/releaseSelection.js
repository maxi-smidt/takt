export function compareVersions(a, b) {
  const partsA = String(a).split(".");
  const partsB = String(b).split(".");
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
