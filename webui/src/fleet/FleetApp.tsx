// @ts-nocheck
import { useState } from "react";
import "../shared/ui/ui.css";
import "./styles.css";
import { Zap } from "lucide-react";
import { useSession } from "./hooks/useSession";
import { Login } from "./components/Login";
import { Dashboard } from "./components/Dashboard";
import { Portal } from "./components/Portal";
import { PasswordChange } from "./components/PasswordChange";

function App() {
  const { session, loading, refreshSession } = useSession();
  // Admins can switch into the runs portal to see what operators see; this
  // only ever applies to an admin session — a non-admin never gets the
  // Dashboard regardless of this flag.
  const [showRunsAsAdmin, setShowRunsAsAdmin] = useState(false);
  if (loading) return <div className="boot-screen"><Zap size={22} /> TAKT FLEET</div>;

  if (session?.user?.must_change_password) return <PasswordChange session={session} refreshSession={refreshSession} />;
  if (!session) return <Login onLogin={refreshSession} />;

  const isAdmin = session.user?.is_admin ?? true;
  if (isAdmin && !showRunsAsAdmin) {
    return (
      <Dashboard session={session} refreshSession={refreshSession} onSwitchToRuns={() => setShowRunsAsAdmin(true)} />
    );
  }
  return (
    <Portal
      session={session}
      refreshSession={refreshSession}
      onSwitchToAdmin={isAdmin ? () => setShowRunsAsAdmin(false) : undefined}
    />
  );
}
export default App;
