// @ts-nocheck
import { useState } from "react";
import { KeyRound, Zap } from "lucide-react";
import { request } from "../services/fleetService";

export function Login({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await request("/api/session", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      await onLogin();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="login-shell">
      <section className="login-card">
        <div className="brand-mark"><Zap size={24} /></div>
        <span className="eyebrow">DEVICE CONTROL PLANE</span>
        <h1>TAKT <em>FLEET</em></h1>
        <p>Manage every timing unit from one secure registry.</p>
        <form onSubmit={submit}>
          <label>
            <span>USERNAME</span>
            <input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="Username" autoComplete="username" />
            <span>PASSWORD</span>
            <div className="password-field">
              <KeyRound size={16} />
              <input
                type="password"
                autoFocus
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="Enter registry password"
              />
            </div>
          </label>
          {error && <div className="form-error">{error}</div>}
          <button className="primary-button full-width" disabled={busy || !password || !username}>
            {busy ? "CONNECTING …" : "OPEN REGISTRY"}
          </button>
        </form>
      </section>
    </main>
  );
}
