import { useCallback, useEffect, useState } from "react";
import { request } from "../services/fleetService";

export function useUserAdmin({ csrf }) {
  const [users, setUsers] = useState([]);
  const [error, setError] = useState("");
  const [temporaryPassword, setTemporaryPassword] = useState("");

  const load = useCallback(async () => {
    try {
      setUsers((await request("/api/admin/users")).users || []);
    } catch (failure) {
      setError(failure.message);
    }
  }, []);
  useEffect(() => {
    queueMicrotask(load);
  }, [load]);

  const create = async (username) => {
    try {
      const result = await request(
        "/api/admin/users",
        { method: "POST", body: JSON.stringify({ username }) },
        csrf,
      );
      setTemporaryPassword(result.temporary_password);
      await load();
      return true;
    } catch (failure) {
      setError(failure.message);
      return false;
    }
  };

  const changeState = async (user) => {
    try {
      await request(
        "/api/admin/users/" + user.id,
        { method: "PATCH", body: JSON.stringify({ disabled: !user.disabled }) },
        csrf,
      );
      await load();
    } catch (failure) {
      setError(failure.message);
    }
  };

  const reset = async (user) => {
    try {
      const result = await request(
        "/api/admin/users/" + user.id + "/reset-password",
        { method: "POST", body: JSON.stringify({}) },
        csrf,
      );
      setTemporaryPassword(result.temporary_password);
    } catch (failure) {
      setError(failure.message);
    }
  };

  return { users, error, temporaryPassword, load, create, changeState, reset };
}
