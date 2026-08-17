import { useEffect, useState, type FormEvent } from "react";
import { Activity, Check, KeyRound, Plus, RotateCcw, X } from "lucide-react";
import type { Deployment, DeploymentEvent, Release } from "../../shared/contracts";
import { parseDeploymentResponse } from "../../shared/contracts";
import { Button, Callout, Field, Select, TextInput } from "../../shared/ui";
import { deploymentTargetError, hostnameChangeError } from "../deploymentValidation.js";
import { insecureRemoteHttp } from "../formatters";
import { preferredReleaseId } from "../releaseSelection.js";
import { openDeploymentEvents, request } from "../services/fleetService";
import { Modal } from "./Modal";

interface EnrollmentFields {
  device_name: string;
  hostname: string;
  confirm_hostname_change: boolean;
  ssh_user: string;
  target: string;
  registry_url: string;
  release_id: string;
  allow_insecure_http: boolean;
}

interface Credentials {
  ssh_password: string;
  ssh_private_key: string;
  ssh_key_passphrase: string;
  sudo_password: string;
}

type TextFieldKey = "device_name" | "hostname" | "ssh_user" | "target" | "registry_url";

const TEXT_FIELDS: [TextFieldKey, string, string][] = [
  ["device_name", "DEVICE DISPLAY NAME", "Bahn 1"],
  ["hostname", "NEW SYSTEM HOSTNAME (OPTIONAL)", "Leave empty to preserve the Pi hostname"],
  ["ssh_user", "SSH USER", "pi"],
  ["target", "SSH ADDRESS / CURRENT HOSTNAME", "raspberrypi.local"],
  ["registry_url", "REGISTRY URL REACHABLE BY THE PI", "https://registry.example"],
];

interface EnrollmentModalProps {
  csrf: string;
  onClose: () => void;
  releases: Release[];
  onDone: () => void;
}

export function EnrollmentModal({ csrf, onClose, releases, onDone }: EnrollmentModalProps) {
  const [fields, setFields] = useState<EnrollmentFields>({
    device_name: "Bahn 1",
    hostname: "",
    confirm_hostname_change: false,
    ssh_user: "",
    target: "raspberrypi.local",
    registry_url: window.location.origin,
    release_id: preferredReleaseId(releases),
    allow_insecure_http: false,
  });
  const [deployment, setDeployment] = useState<Deployment | null>(null);
  const [events, setEvents] = useState<DeploymentEvent[]>([]);
  const [credentials, setCredentials] = useState<Credentials>({
    ssh_password: "", ssh_private_key: "", ssh_key_passphrase: "", sudo_password: "",
  });
  const [replaceHostKey, setReplaceHostKey] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const [streamAfter, setStreamAfter] = useState(0);
  const update = <K extends keyof EnrollmentFields>(key: K, value: EnrollmentFields[K]) =>
    setFields((current) => ({ ...current, [key]: value, ...(key === "hostname" ? { confirm_hostname_change: false } : {}) }));
  const validate = () => {
    if (!/^[A-Za-z0-9ÄÖÜäöüß._ -]{1,80}$/.test(fields.device_name.trim())) return "Device name is invalid.";
    const hostnameError = hostnameChangeError(fields.hostname, fields.confirm_hostname_change);
    if (hostnameError) return hostnameError;
    if (!/^[A-Za-z_][A-Za-z0-9_-]{0,31}$/.test(fields.ssh_user)) return "SSH user is invalid.";
    if (deploymentTargetError(fields.target)) return "Target is invalid.";
    if (!fields.release_id) return "Upload a Raspberry Pi release first.";
    try {
      const url = new URL(fields.registry_url);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return "Registry URL is invalid.";
    } catch {
      return "Registry URL is invalid.";
    }
    if (insecureRemoteHttp(fields.registry_url) && !fields.allow_insecure_http) return "HTTPS or explicit HTTP acknowledgement is required.";
    return "";
  };

  const post = async (path: string, body: unknown) => {
    setBusy(true);
    setError("");
    try {
      const result = parseDeploymentResponse(await request(path, { method: "POST", body: JSON.stringify(body) }, csrf));
      setDeployment(result.deployment);
      return true;
    } catch (failure) {
      setError((failure as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const create = async (event: FormEvent) => {
    event.preventDefault();
    const validationError = validate();
    if (validationError) return setError(validationError);
    const result = await post("/api/deployments", fields);
    if (result) setEvents([]);
  };

  useEffect(() => {
    if (!deployment?.id) return undefined;
    let active = true;
    const path = "/api/deployments/" + deployment.id;
    const load = () => request(path)
      .then((raw) => {
        if (!active) return;
        const result = parseDeploymentResponse(raw);
        setDeployment(result.deployment);
        setError("");
        if (["succeeded", "failed", "cancelled", "interrupted"].includes(result.deployment.status)) {
          source?.close();
        }
      })
      .catch((failure) => active && setError((failure as Error).message));
    const source = openDeploymentEvents(
      deployment.id,
      streamAfter,
      (event) => {
        if (!active) return;
        setEvents((current) => [...current, event]);
        if (event.deployment) {
          setDeployment(event.deployment);
          setError("");
          if (["succeeded", "failed", "cancelled", "interrupted"].includes(event.deployment.status)) source.close();
        }
      },
      (failure) => { if (active) setError(failure.message); },
    );
    load();
    source.onerror = () => { if (active) load(); };
    return () => { active = false; source.close(); };
  }, [deployment?.id, streamAfter]);

  const confirmHostKey = () => post(
    "/api/deployments/" + deployment!.id + "/host-key",
    { fingerprint: deployment!.host_key_fingerprint, replace: replaceHostKey },
  );
  const submitCredentials = async (event: FormEvent) => {
    event.preventDefault();
    if (!credentials.ssh_password && !credentials.ssh_private_key) return setError("SSH password or private key is required.");
    const accepted = await post("/api/deployments/" + deployment!.id + "/credentials", credentials);
    if (accepted) setCredentials({ ssh_password: "", ssh_private_key: "", ssh_key_passphrase: "", sudo_password: "" });
  };
  const cancel = () => post("/api/deployments/" + deployment!.id + "/cancel", {});
  const retry = async () => {
    const after = events.at(-1)?.id || 0;
    if (await post("/api/deployments/" + deployment!.id + "/retry", {})) {
      setEvents([]);
      setStreamAfter(after);
    }
  };

  return (
    <Modal title="DEPLOY A RASPBERRY PI" eyebrow="GUIDED FIRST DEPLOYMENT" onClose={onClose} wide>
      {!deployment ? (
        <form className="modal-body" onSubmit={create}>
          <p>The registry checks, installs, enrolls, and verifies the Pi without a laptop checkout.</p>
          <div className="enrollment-fields">
            {TEXT_FIELDS.map(([key, label, placeholder]) => (
              <Field label={label} key={key}>
                {(fieldProps) => (
                  <TextInput
                    {...fieldProps}
                    value={fields[key]}
                    placeholder={placeholder}
                    onChange={(event) => update(key, event.target.value)}
                    required={key !== "hostname"}
                  />
                )}
              </Field>
            ))}
            {fields.hostname && (
              <label className="insecure-opt-in">
                <input type="checkbox" checked={fields.confirm_hostname_change} onChange={(event) => update("confirm_hostname_change", event.target.checked)} />
                <span><strong>CONFIRM HOSTNAME CHANGE</strong> Preview: the Pi will move to <code>{fields.hostname}.local</code>; mDNS, DHCP, SSH host keys, and reconnect behavior can change.</span>
              </label>
            )}
            <Field label="RASPBERRY PI RELEASE">
              {(fieldProps) => (
                <Select
                  {...fieldProps}
                  value={fields.release_id}
                  onValueChange={(value) => update("release_id", value)}
                  placeholder="SELECT A RELEASE"
                  options={releases.map((release) => ({
                    value: release.id,
                    label: release.version + (release.source === "bundled" ? " · VERIFIED" : ""),
                  }))}
                />
              )}
            </Field>
            {insecureRemoteHttp(fields.registry_url) && (
              <label className="insecure-opt-in"><input type="checkbox" checked={fields.allow_insecure_http} onChange={(event) => update("allow_insecure_http", event.target.checked)} /><span><strong>ALLOW HTTP TRANSPORT</strong> Use only on a protected VPN or isolated LAN.</span></label>
            )}
          </div>
          {error && <Callout tone="danger">{error}</Callout>}
          <Button type="submit" variant="primary" className="full-width" disabled={busy || !releases.length}>
            <Plus size={15} /> {busy ? "STARTING …" : "START DEPLOYMENT"}
          </Button>
        </form>
      ) : (
        <div className="modal-body deployment-panel">
          <div className="deployment-ready"><Activity size={22} /><div><strong>{deployment.status.replaceAll("_", " ").toUpperCase()}</strong><span>{deployment.message}</span></div></div>
          <div className="deployment-log" aria-live="polite">{events.map((item) => <div className={"deployment-log-line level-" + item.level} key={item.id}><span>{item.stage}</span>{item.message}</div>)}</div>
          {deployment.status === "awaiting_host_key" && (
            <div className="deployment-confirmation">
              <strong>VERIFY SSH HOST KEY</strong>
              <code>{deployment.host_key_fingerprint}</code>
              <label className="insecure-opt-in">
                <input type="checkbox" checked={replaceHostKey} onChange={(event) => setReplaceHostKey(event.target.checked)} />
                <span>Replace an existing trusted key only when expected.</span>
              </label>
              <Button variant="primary" className="full-width" onClick={confirmHostKey} disabled={busy}>
                <KeyRound size={15} /> TRUST HOST KEY
              </Button>
            </div>
          )}
          {deployment.status === "awaiting_credentials" && (
            <form className="deployment-credentials" onSubmit={submitCredentials}>
              <Field label="SSH PASSWORD">
                {(fieldProps) => (
                  <TextInput
                    {...fieldProps}
                    type="password"
                    autoComplete="new-password"
                    value={credentials.ssh_password}
                    onChange={(event) => setCredentials({ ...credentials, ssh_password: event.target.value })}
                  />
                )}
              </Field>
              <Field label="OR PRIVATE KEY">
                {(fieldProps) => (
                  <textarea
                    {...fieldProps}
                    className="takt-input"
                    rows={4}
                    value={credentials.ssh_private_key}
                    onChange={(event) => setCredentials({ ...credentials, ssh_private_key: event.target.value })}
                  />
                )}
              </Field>
              <Field label="KEY PASSPHRASE">
                {(fieldProps) => (
                  <TextInput
                    {...fieldProps}
                    type="password"
                    autoComplete="off"
                    value={credentials.ssh_key_passphrase}
                    onChange={(event) => setCredentials({ ...credentials, ssh_key_passphrase: event.target.value })}
                  />
                )}
              </Field>
              <Field label="SUDO PASSWORD">
                {(fieldProps) => (
                  <TextInput
                    {...fieldProps}
                    type="password"
                    autoComplete="off"
                    value={credentials.sudo_password}
                    onChange={(event) => setCredentials({ ...credentials, sudo_password: event.target.value })}
                    placeholder="Defaults to SSH password"
                  />
                )}
              </Field>
              <Button type="submit" variant="primary" className="full-width" disabled={busy}>
                <KeyRound size={15} /> CONTINUE
              </Button>
            </form>
          )}
          {error && <Callout tone="danger">{error}</Callout>}
          <div className="deployment-actions">
            {["pending", "running", "awaiting_host_key", "awaiting_credentials"].includes(deployment.status) && (
              <Button variant="secondary" onClick={cancel} disabled={busy}>
                <X size={15} /> CANCEL
              </Button>
            )}
            {["failed", "cancelled", "interrupted"].includes(deployment.status) && (
              <Button variant="secondary" onClick={retry} disabled={busy}>
                <RotateCcw size={15} /> RETRY
              </Button>
            )}
            {deployment.status === "succeeded" && (
              <Button variant="primary" onClick={() => { onDone(); onClose(); }}>
                <Check size={15} /> DONE
              </Button>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}
