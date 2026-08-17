import { useState, type FormEvent } from "react";
import { Button, Callout, Field, TextInput } from "../../shared/ui";
import { request } from "../services/fleetService";

interface PasswordChangeProps {
  session: { csrf_token: string };
  refreshSession: () => Promise<void>;
}

export function PasswordChange({ session, refreshSession }: PasswordChangeProps) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
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
      setError((failure as Error).message);
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
          <Field label="CURRENT PASSWORD">
            {(fieldProps) => (
              <TextInput
                {...fieldProps}
                type="password"
                value={currentPassword}
                onChange={(event) => setCurrentPassword(event.target.value)}
                autoComplete="current-password"
              />
            )}
          </Field>
          <Field label="NEW PASSWORD">
            {(fieldProps) => (
              <TextInput
                {...fieldProps}
                type="password"
                minLength={12}
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
                autoComplete="new-password"
              />
            )}
          </Field>
          {error && <Callout tone="danger">{error}</Callout>}
          <Button type="submit" variant="primary" disabled={busy || !currentPassword || newPassword.length < 12}>
            {busy ? "SAVING …" : "SET PASSWORD"}
          </Button>
        </form>
      </main>
    </div>
  );
}
