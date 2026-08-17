import { useState, type FormEvent } from "react";
import { Trash2, Upload } from "lucide-react";
import type { Release } from "../../shared/contracts";
import { Badge, Button, Callout, Field, IconButton, TextInput } from "../../shared/ui";
import { bytes, timeAgo } from "../formatters";
import { request } from "../services/fleetService";
import { Modal } from "./Modal";

interface ReleaseModalProps {
  csrf: string;
  releases: Release[];
  onClose: () => void;
  onUploaded: () => Promise<void>;
  onUninstall: (release: Release) => void;
}

export function ReleaseModal({ csrf, releases, onClose, onUploaded, onUninstall }: ReleaseModalProps) {
  const [version, setVersion] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const upload = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData();
    data.append("version", version);
    data.append("artifact", file as File);
    try {
      await request("/api/releases", { method: "POST", body: data }, csrf);
      await onUploaded();
      setVersion("");
      setFile(null);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="RELEASE LIBRARY" eyebrow="VERSION LIBRARY" onClose={onClose} wide>
      <form className="modal-body" onSubmit={upload}>
        <p>Upload the Raspberry Pi package created by <code>package_for_raspberry_pi.sh</code>.</p>
        <Field label="VERSION">
          {(fieldProps) => (
            <TextInput {...fieldProps} value={version} onChange={(event) => setVersion(event.target.value)} placeholder="e.g. 0.2.0" />
          )}
        </Field>
        <label className="file-drop">
          <Upload size={22} />
          <strong>{file ? file.name : "SELECT .TAR.GZ RELEASE"}</strong>
          <small>{file ? bytes(file.size) : "Maximum 250 MB"}</small>
          <input type="file" accept=".gz,.tar.gz" onChange={(event) => setFile(event.target.files?.[0] || null)} />
        </label>
        {error && <Callout tone="danger">{error}</Callout>}
        <Button type="submit" variant="primary" className="full-width" disabled={busy || !file || !version}>
          {busy ? "UPLOADING …" : "STORE RELEASE"}
        </Button>
      </form>
      <div className="release-list">
        {!releases.length && <p className="release-list-empty">No releases uploaded yet.</p>}
        {releases.map((release) => (
          <div className="release-row" key={release.id}>
            <div>
              <strong>{release.version}</strong>
              <small>
                {release.source === "bundled" ? "VERIFIED · " : ""}
                {bytes(release.size)} · {timeAgo(release.created_at)}
              </small>
            </div>
            <Badge tone={release.installed ? "success" : "neutral"}>
              {release.installed ? "CACHED" : "NOT CACHED · REDOWNLOADS ON INSTALL"}
            </Badge>
            <IconButton
              variant="danger"
              icon={<Trash2 size={15} />}
              aria-label={`Uninstall ${release.version}`}
              disabled={!release.installed}
              onClick={() => onUninstall(release)}
            />
          </div>
        ))}
      </div>
    </Modal>
  );
}
