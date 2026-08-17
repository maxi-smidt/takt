import { useState, type FormEvent } from "react";
import { RefreshCw } from "lucide-react";
import type { Device } from "../../shared/contracts";
import { Badge, Button, Callout, Field, TextInput } from "../../shared/ui";
import { useUserAdmin } from "../hooks/useUserAdmin";
import { AccessModal } from "./AccessModal";

interface UserAdminPanelProps {
  csrf: string;
  devices: Device[];
}

export function UserAdminPanel({ csrf, devices }: UserAdminPanelProps) {
  const { users, error, temporaryPassword, load, create, changeState, reset } = useUserAdmin({ csrf });
  const [username, setUsername] = useState("");
  const [accessUserId, setAccessUserId] = useState<string | null>(null);

  const submitCreate = async (event: FormEvent) => {
    event.preventDefault();
    if (await create(username)) setUsername("");
  };

  const deviceName = (deviceId: string) => devices.find((device) => device.id === deviceId)?.name || deviceId;
  const accessUser = users.find((user) => user.id === accessUserId) || null;

  return (
    <section className="operations">
      <div className="section-heading">
        <div><span>03 · ACCESS</span><h2>USERS AND DEVICE ACCESS</h2></div>
        <Button variant="secondary" onClick={load}><RefreshCw size={14} /> REFRESH</Button>
      </div>
      <form className="enrollment-fields" onSubmit={submitCreate}>
        <Field label="USERNAME">
          {(fieldProps) => <TextInput {...fieldProps} value={username} onChange={(event) => setUsername(event.target.value)} />}
        </Field>
        <Button type="submit" variant="primary" disabled={!username}>CREATE USER</Button>
      </form>
      {temporaryPassword && (
        <Callout tone="warning"><strong>ONE-TIME PASSWORD:</strong> <code>{temporaryPassword}</code></Callout>
      )}
      {error && <Callout tone="danger">{error}</Callout>}
      <div className="user-list">
        {users.map((user) => (
          <div className="user-row" key={user.id}>
            <div className="job-copy">
              <strong>{user.username}{user.is_admin ? " · ADMIN" : ""}</strong>
              <span>{user.disabled ? "DISABLED" : "ACTIVE"}</span>
            </div>
            <div className="access-summary">
              {(user.access || []).length
                ? user.access!.map((item) => (
                    <Badge key={item.device_id}>
                      {deviceName(item.device_id)} · {item.access_level.toUpperCase()}
                    </Badge>
                  ))
                : <span className="access-badge access-badge-empty">NO DEVICE ACCESS</span>}
            </div>
            <div className="user-row-actions">
              <Button variant="secondary" size="sm" onClick={() => setAccessUserId(user.id)}>MANAGE ACCESS</Button>
              <Button variant="secondary" size="sm" onClick={() => reset(user)}>RESET PASSWORD</Button>
              <Button variant="secondary" size="sm" onClick={() => changeState(user)}>
                {user.disabled ? "ENABLE" : "DISABLE"}
              </Button>
            </div>
          </div>
        ))}
        {!users.length && <p className="access-empty-state">No users yet.</p>}
      </div>
      {accessUser && (
        <AccessModal
          user={accessUser}
          devices={devices}
          csrf={csrf}
          onClose={() => setAccessUserId(null)}
          onChanged={load}
        />
      )}
    </section>
  );
}
