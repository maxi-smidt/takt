// @ts-nocheck
import { useState } from "react";
import { Trash2, Upload } from "lucide-react";
import { request } from "../services/fleetService";
import { bytes, timeAgo } from "../formatters";
import { Modal } from "./Modal";

export function ReleaseModal({ csrf, releases, onClose, onUploaded, onUninstall }) {
  const [version, setVersion] = useState("");
  const [file, setFile] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const upload = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData();
    data.append("version", version);
    data.append("artifact", file);
    try {
      await request("/api/releases", { method: "POST", body: data }, csrf);
      await onUploaded();
      setVersion("");
      setFile(null);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="RELEASE LIBRARY" eyebrow="VERSION LIBRARY" onClose={onClose} wide>
      <form className="modal-body" onSubmit={upload}>
        <p>Upload the Raspberry Pi package created by <code>package_for_raspberry_pi.sh</code>.</p>
        <label className="field-label">VERSION
          <input value={version} onChange={(event) => setVersion(event.target.value)} placeholder="e.g. 0.2.0" />
        </label>
        <label className="file-drop">
          <Upload size={22} />
          <strong>{file ? file.name : "SELECT .TAR.GZ RELEASE"}</strong>
          <small>{file ? bytes(file.size) : "Maximum 250 MB"}</small>
          <input type="file" accept=".gz,.tar.gz" onChange={(event) => setFile(event.target.files[0] || null)} />
        </label>
        {error && <div className="form-error">{error}</div>}
        <button className="primary-button full-width" disabled={busy || !file || !version}>
          {busy ? "UPLOADING …" : "STORE RELEASE"}
        </button>
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
            <span className={`release-status ${release.installed ? "is-cached" : "is-uninstalled"}`}>
              {release.installed ? "CACHED" : "NOT CACHED · REDOWNLOADS ON INSTALL"}
            </span>
            <button
              className="icon-button danger-action"
              title={`Uninstall ${release.version}`}
              disabled={!release.installed}
              onClick={() => onUninstall(release)}
            ><Trash2 size={15} /></button>
          </div>
        ))}
      </div>
    </Modal>
  );
}
