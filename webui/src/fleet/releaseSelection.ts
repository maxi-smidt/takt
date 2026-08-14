import type { Release } from "../shared/contracts";

function compareCore(partsA: string[], partsB: string[]): number {
  const length = Math.max(partsA.length, partsB.length);
  for (let index = 0; index < length; index += 1) {
    const partA = partsA[index] ?? "";
    const partB = partsB[index] ?? "";
    const numberA = Number(partA);
    const numberB = Number(partB);
    if (Number.isNaN(numberA) || Number.isNaN(numberB)) {
      if (partA !== partB) return partA < partB ? -1 : 1;
    } else if (numberA !== numberB) return numberA < numberB ? -1 : 1;
  }
  return 0;
}
function compareIdentifiers(a: string, b: string): number {
  const numberA = /^\d+$/.test(a) ? Number(a) : null;
  const numberB = /^\d+$/.test(b) ? Number(b) : null;
  if (numberA !== null && numberB !== null)
    return numberA === numberB ? 0 : numberA < numberB ? -1 : 1;
  if (numberA !== null) return -1;
  if (numberB !== null) return 1;
  return a === b ? 0 : a < b ? -1 : 1;
}
function comparePrerelease(a: string[], b: string[]): number {
  if (!a.length && !b.length) return 0;
  if (!a.length) return 1;
  if (!b.length) return -1;
  const length = Math.max(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    if (index >= a.length) return -1;
    if (index >= b.length) return 1;
    const comparison = compareIdentifiers(a[index]!, b[index]!);
    if (comparison !== 0) return comparison;
  }
  return 0;
}
function splitVersion(value: string): { core: string[]; prerelease: string[] } {
  const [core = "", ...parts] = value.split("-");
  return {
    core: core.split("."),
    prerelease: parts.length ? parts.join("-").split(".") : [],
  };
}
export function compareVersions(a: string, b: string): number {
  const versionA = splitVersion(String(a));
  const versionB = splitVersion(String(b));
  const coreComparison = compareCore(versionA.core, versionB.core);
  return (
    coreComparison ||
    comparePrerelease(versionA.prerelease, versionB.prerelease)
  );
}
export function preferredReleaseId(releases: Release[]): string {
  const verified = releases.filter((release) => release.source === "bundled");
  if (!verified.length) return releases[0]?.id || "";
  return verified.reduce((best, release) =>
    compareVersions(release.version, best.version) > 0 ? release : best,
  ).id;
}
