// @ts-nocheck
import { useState } from "react";
import { request } from "../services/fleetService";

export function PasswordChange({ session, refreshSession }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await request(
        "/api/session/password",
        {
          method: "POST",
          body: JSON.stringify({
            current_password: currentPassword,
            new_password: newPassword,
          }),
        },
        session.csrf_token,
      );
      await refreshSession();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="fleet-app">
      <main>
        <section className="hero">
          <div>
            <span className="eyebrow">SECURITY</span>
            <h1>CHANGE PASSWORD</h1>
            <p>Your temporary password must be replaced before continuing.</p>
          </div>
        </section>
        <form className="enrollment-fields" onSubmit={submit}>
          <label className="field-label">CURRENT PASSWORD
            <input
              type="password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              autoComplete="current-password"
            />
          </label>
          <label className="field-label">NEW PASSWORD
            <input
              type="password"
              minLength={12}
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              autoComplete="new-password"
            />
          </label>
          {error && <div className="form-error">{error}</div>}
          <button
            className="primary-button"
            disabled={busy || !currentPassword || newPassword.length < 12}
          >
            {busy ? "SAVING …" : "SET PASSWORD"}
          </button>
        </form>
      </main>
    </div>
  );
}
