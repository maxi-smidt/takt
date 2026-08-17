// @ts-nocheck
import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useUserAdmin } from "../hooks/useUserAdmin";
import { AccessModal } from "./AccessModal";

export function UserAdminPanel({ csrf, devices }) {
  const { users, error, temporaryPassword, load, create, changeState, reset } = useUserAdmin({ csrf });
  const [username, setUsername] = useState("");
  const [accessUserId, setAccessUserId] = useState(null);

  const submitCreate = async (event) => {
    event.preventDefault();
    if (await create(username)) setUsername("");
  };

  const deviceName = (deviceId) => devices.find((device) => device.id === deviceId)?.name || deviceId;
  const accessUser = users.find((user) => user.id === accessUserId) || null;

  return (
    <section className="operations">
      <div className="section-heading">
        <div><span>03 · ACCESS</span><h2>USERS AND DEVICE ACCESS</h2></div>
        <button onClick={load}><RefreshCw size={14} /> REFRESH</button>
      </div>
      <form className="enrollment-fields" onSubmit={submitCreate}>
        <label className="field-label">USERNAME
          <input value={username} onChange={(event) => setUsername(event.target.value)} />
        </label>
        <button className="primary-button" disabled={!username}>CREATE USER</button>
      </form>
      {temporaryPassword && (
        <div className="security-warning"><strong>ONE-TIME PASSWORD:</strong> <code>{temporaryPassword}</code></div>
      )}
      {error && <div className="form-error">{error}</div>}
      <div className="user-list">
        {users.map((user) => (
          <div className="user-row" key={user.id}>
            <div className="job-copy">
              <strong>{user.username}{user.is_admin ? " · ADMIN" : ""}</strong>
              <span>{user.disabled ? "DISABLED" : "ACTIVE"}</span>
            </div>
            <div className="access-summary">
              {(user.access || []).length
                ? user.access.map((item) => (
                    <span className="access-badge" key={item.device_id}>
                      {deviceName(item.device_id)} · {item.access_level.toUpperCase()}
                    </span>
                  ))
                : <span className="access-badge access-badge-empty">NO DEVICE ACCESS</span>}
            </div>
            <div className="user-row-actions">
              <button className="secondary-button" onClick={() => setAccessUserId(user.id)}>MANAGE ACCESS</button>
              <button className="secondary-button" onClick={() => reset(user)}>RESET PASSWORD</button>
              <button className="secondary-button" onClick={() => changeState(user)}>
                {user.disabled ? "ENABLE" : "DISABLE"}
              </button>
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
