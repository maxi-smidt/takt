// @ts-nocheck
import { useState } from "react";
import { request } from "../services/fleetService";
import { Modal } from "./Modal";

export function AccessModal({ user, devices, csrf, onClose, onChanged }) {
  const [error, setError] = useState("");
  const [busyDeviceId, setBusyDeviceId] = useState(null);

  const accessFor = (deviceId) =>
    (user.access || []).find((item) => item.device_id === deviceId)?.access_level || "none";

  const setAccess = async (deviceId, level) => {
    setError("");
    setBusyDeviceId(deviceId);
    try {
      if (level === "none") {
        await request(`/api/admin/users/${user.id}/devices/${deviceId}`, { method: "DELETE" }, csrf);
      } else {
        await request(
          `/api/admin/users/${user.id}/devices/${deviceId}`,
          { method: "PUT", body: JSON.stringify({ access: level }) },
          csrf,
        );
      }
      await onChanged();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusyDeviceId(null);
    }
  };

  return (
    <Modal title={`DEVICE ACCESS · ${user.username}`} eyebrow="ACCESS CONTROL" onClose={onClose} wide>
      <div className="modal-body access-fields">
        <p>Grant or revoke access per device. Changes apply immediately and are audited.</p>
        {error && <div className="form-error">{error}</div>}
        <div className="access-list">
          {devices.map((device) => (
            <div className="access-row" key={device.id}>
              <span className="access-device-name">{device.name}</span>
              <select
                value={accessFor(device.id)}
                disabled={busyDeviceId === device.id}
                onChange={(event) => setAccess(device.id, event.target.value)}
              >
                <option value="none">NO ACCESS</option>
                <option value="read">READ</option>
                <option value="write">WRITE</option>
              </select>
            </div>
          ))}
          {!devices.length && <p>No devices enrolled yet.</p>}
        </div>
      </div>
    </Modal>
  );
}
