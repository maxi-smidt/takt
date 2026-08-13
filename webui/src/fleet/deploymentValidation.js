export function deploymentTargetError(target) {
  return target.length <= 253 && (/^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$/.test(target) || /^[0-9A-Fa-f:]+$/.test(target))
    ? ""
    : "Target is invalid.";
}
