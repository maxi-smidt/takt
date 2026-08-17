import { useState } from "react";
import type { Device } from "../../shared/contracts";
import { Callout, Select, type SelectOption } from "../../shared/ui";
import { request } from "../services/fleetService";
import { Modal } from "./Modal";

export interface AdminUser {
  id: string;
  username: string;
  is_admin?: boolean;
  disabled?: boolean;
  access?: { device_id: string; access_level: string }[];
}

interface AccessModalProps {
  user: AdminUser;
  devices: Device[];
  csrf: string;
  onClose: () => void;
  onChanged: () => Promise<void>;
}

const ACCESS_OPTIONS: SelectOption[] = [
  { value: "none", label: "NO ACCESS" },
  { value: "read", label: "READ" },
  { value: "write", label: "WRITE" },
];

export function AccessModal({ user, devices, csrf, onClose, onChanged }: AccessModalProps) {
  const [error, setError] = useState("");
  const [busyDeviceId, setBusyDeviceId] = useState<string | null>(null);

  const accessFor = (deviceId: string) =>
    (user.access || []).find((item) => item.device_id === deviceId)?.access_level || "none";

  const setAccess = async (deviceId: string, level: string) => {
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
      setError((failure as Error).message);
    } finally {
      setBusyDeviceId(null);
    }
  };

  return (
    <Modal title={`DEVICE ACCESS · ${user.username}`} eyebrow="ACCESS CONTROL" onClose={onClose} wide>
      <div className="modal-body access-fields">
        <p>Grant or revoke access per device. Changes apply immediately and are audited.</p>
        {error && <Callout tone="danger">{error}</Callout>}
        <div className="access-list">
          {devices.map((device) => (
            <div className="access-row" key={device.id}>
              <span className="access-device-name">{device.name}</span>
              <Select
                className="access-select"
                value={accessFor(device.id)}
                disabled={busyDeviceId === device.id}
                onValueChange={(value) => setAccess(device.id, value)}
                options={ACCESS_OPTIONS}
              />
            </div>
          ))}
          {!devices.length && <p>No devices enrolled yet.</p>}
        </div>
      </div>
    </Modal>
  );
}
