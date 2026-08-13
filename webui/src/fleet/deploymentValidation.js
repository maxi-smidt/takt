export function deploymentTargetError(target) {
  return target.length <= 253 && (/^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$/.test(target) || /^[0-9A-Fa-f:]+$/.test(target))
    ? ""
    : "Target is invalid.";
}


export function hostnameChangeError(hostname, confirmed) {
  if (!hostname) return "";
  if (!/^[A-Za-z0-9][A-Za-z0-9-]{0,62}$/.test(hostname)) return "Hostname is invalid.";
  return confirmed ? "" : "Confirm the requested hostname change before deployment.";
}
