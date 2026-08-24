import { useState, type FormEvent } from "react";
import { KeyRound, Zap } from "lucide-react";
import { Button, Callout, Field, TextInput } from "../../shared/ui";
import { request } from "../services/fleetService";

interface LoginProps {
  onLogin: () => Promise<void>;
}

export function Login({ onLogin }: LoginProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
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
      setError((failure as Error).message);
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
        <form className="login-form" onSubmit={submit}>
          <Field label="USERNAME">
            {(fieldProps) => (
              <TextInput
                {...fieldProps}
                name="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder="Username"
                autoComplete="username"
              />
            )}
          </Field>
          <Field label="PASSWORD">
            {(fieldProps) => (
              <div className="password-field">
                <KeyRound size={16} />
                <input
                  {...fieldProps}
                  type="password"
                  name="current-password"
                  autoComplete="current-password"
                  autoFocus
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="Enter registry password"
                />
              </div>
            )}
          </Field>
          {error && <Callout tone="danger">{error}</Callout>}
          <Button type="submit" variant="primary" className="full-width" disabled={busy || !password || !username}>
            {busy ? "CONNECTING …" : "OPEN REGISTRY"}
          </Button>
        </form>
      </section>
    </main>
  );
}
