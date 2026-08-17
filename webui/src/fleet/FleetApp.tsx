// @ts-nocheck
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
  if (loading) return <div className="boot-screen"><Zap size={22} /> TAKT FLEET</div>;

  if (session?.user?.must_change_password) return <PasswordChange session={session} refreshSession={refreshSession} />;
  return session ? ((session.user?.is_admin ?? true) ? <Dashboard session={session} refreshSession={refreshSession} /> : <Portal session={session} refreshSession={refreshSession} />) : <Login onLogin={refreshSession} />;
}
export default App;
