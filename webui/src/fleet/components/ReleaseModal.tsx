// @ts-nocheck
import { useState } from "react";
import { Upload } from "lucide-react";
import { request } from "../services/fleetService";
import { bytes } from "../formatters";
import { Modal } from "./Modal";

export function ReleaseModal({ csrf, onClose, onUploaded }) {
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
      onClose();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="ADD A TAKT RELEASE" eyebrow="VERSION LIBRARY" onClose={onClose}>
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
    </Modal>
  );
}
