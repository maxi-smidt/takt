// @ts-nocheck
import { useState } from "react";
import { Wifi } from "lucide-react";
import { request } from "../services/fleetService";
import { wifiNetworkError } from "../wifiValidation.js";
import { Modal } from "./Modal";

export function WifiModal({ device, csrf, onClose, onCreated }) {
  const [ssid, setSsid] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event) => {
    event.preventDefault();
    setError("");
    const validationError = wifiNetworkError(ssid, password);
    if (validationError) {
      setError(validationError);
      return;
    }
    setBusy(true);
    try {
      await request(
        `/api/devices/${device.id}/wifi-networks`,
        { method: "POST", body: JSON.stringify({ ssid, password }) },
        csrf,
      );
      setPassword("");
      await onCreated();
      onClose();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`ADD WI-FI TO ${device.name}`} eyebrow="NETWORK PROFILE" onClose={onClose}>
      <form className="modal-body wifi-fields" onSubmit={submit}>
        <p>
          Save a WPA/WPA2 network without switching the current connection. The profile uses
          the default priority <strong>0</strong>. Send credentials only over HTTPS or a private VPN.
        </p>
        <label className="field-label">SSID
          <input
            autoFocus
            value={ssid}
            onChange={(event) => setSsid(event.target.value)}
            required
          />
        </label>
        <label className="field-label">PASSWORD
          <input
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </label>
        {error && <div className="form-error">{error}</div>}
        <button className="primary-button full-width" disabled={busy || !ssid || !password}>
          <Wifi size={15} /> {busy ? "SAVING …" : "SAVE WI-FI PROFILE"}
        </button>
      </form>
    </Modal>
  );
}
