import { useCallback, useEffect, useRef, useState } from "react";

const EMPTY_STATE = {
  state: "ready",
  state_label: "BEREIT",
  actual_ms: 0,
  actual: "00:00.00",
  added_ms: 0,
  added: "+00:00.00",
  total_ms: 0,
  total: "00:00.00",
  error: null,
  hardware: { label: "Server nicht verbunden", available: false },
  history_revision: 0,
  signal_revision: 0,
  signal: null,
  sound_playing: false,
  start_sequence: {
    active: false,
    phase: null,
    remaining_ms: 0,
    error: null,
  },
  maintenance: {
    held: false,
    reason: null,
    expires_in_seconds: null,
  },
};

const EMPTY_HISTORY = {
  today: [],
  today_count: 0,
  best: [],
  chart: [],
  all: [],
  chart_days: 30,
};

const EMPTY_SYSTEM = {
  shutdown_available: false,
  model: "",
  mock_button: false,
  mock_buzzer: false,
  audio: {
    enabled: false,
    output: "off",
    delay_milliseconds: 3000,
    clip_duration_milliseconds: 17512,
    device_address: null,
    device_name: null,
    playback_available: false,
    bluetooth_available: false,
    sound: "TAKT Startsignal",
    devices: [],
  },
};

const VALID_PERIODS = ["7", "30", "90", "all"];

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `HTTP ${response.status}`);
  }
  return response.json();
}

export function useTaktServer() {
  const storedPeriod = localStorage.getItem("takt-chart-days");
  const [chartDays, setChartDaysState] = useState(
    VALID_PERIODS.includes(storedPeriod) ? storedPeriod : "30",
  );
  const [state, setState] = useState(EMPTY_STATE);
  const [history, setHistory] = useState(EMPTY_HISTORY);
  const [system, setSystem] = useState(EMPTY_SYSTEM);
  const [connection, setConnection] = useState(
    location.protocol === "file:" ? "offline" : "connecting",
  );
  const historyRevision = useRef(-1);
  const socketRef = useRef(null);
  const connectRef = useRef(null);
  const bootstrapRef = useRef(null);
  const retryRef = useRef(null);
  const mountedRef = useRef(true);

  const refreshHistory = useCallback(async (period = chartDays) => {
    if (location.protocol === "file:") return;
    const nextHistory = await fetchJson(`/api/history?days=${period}`);
    if (mountedRef.current) setHistory(nextHistory);
  }, [chartDays]);

  const connectSocket = useCallback(() => {
    if (location.protocol === "file:" || !mountedRef.current) return;
    if (socketRef.current && socketRef.current.readyState < 2) return;
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${location.host}/api/events`);
    socketRef.current = socket;
    setConnection("connecting");
    socket.addEventListener("open", () => {
      if (mountedRef.current) setConnection("online");
    });
    socket.addEventListener("message", async (event) => {
      if (event.data === "pong" || !mountedRef.current) return;
      const message = JSON.parse(event.data);
      if (message.type === "state") {
        const nextState = message.data;
        const changed = (
          historyRevision.current >= 0
          && historyRevision.current !== nextState.history_revision
        );
        historyRevision.current = nextState.history_revision;
        setState(nextState);
        if (changed) {
          try {
            await refreshHistory();
          } catch {
            // The reconnect loop will refresh the complete data set.
          }
        }
      } else if (message.type === "history_changed") {
        try {
          await refreshHistory();
        } catch {
          // The reconnect loop will refresh the complete data set.
        }
      } else if (message.type === "system") {
        setSystem(message.data);
      }
    });
    socket.addEventListener("close", () => {
      if (!mountedRef.current) return;
      setConnection("offline");
      clearTimeout(retryRef.current);
      retryRef.current = setTimeout(() => connectRef.current?.(), 1800);
    });
    socket.addEventListener("error", () => socket.close());
  }, [refreshHistory]);

  const bootstrap = useCallback(async () => {
    if (location.protocol === "file:") return;
    try {
      const data = await fetchJson(`/api/bootstrap?days=${chartDays}`);
      if (!mountedRef.current) return;
      historyRevision.current = data.state.history_revision;
      setState(data.state);
      setHistory(data.history);
      setSystem(data.system);
      connectSocket();
    } catch {
      if (!mountedRef.current) return;
      setConnection("offline");
      clearTimeout(retryRef.current);
      retryRef.current = setTimeout(() => bootstrapRef.current?.(), 1800);
    }
  }, [chartDays, connectSocket]);

  useEffect(() => {
    connectRef.current = connectSocket;
    bootstrapRef.current = bootstrap;
  }, [bootstrap, connectSocket]);

  useEffect(() => {
    mountedRef.current = true;
    queueMicrotask(bootstrap);
    return () => {
      mountedRef.current = false;
      clearTimeout(retryRef.current);
      socketRef.current?.close();
    };
  }, [bootstrap]);

  const sendAction = useCallback(async (action) => {
    const result = await fetchJson("/api/action", {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    setState(result.state);
    return result;
  }, []);

  const sendAudioRequest = useCallback(async (operation, payload = {}) => {
    const result = await fetchJson(`/api/audio/${operation}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setSystem(result.system);
    return result;
  }, []);

  const prepareConfirmation = useCallback(async (operation, runId = null, deltaMs = 0) => (
    fetchJson("/api/confirmations", {
      method: "POST",
      body: JSON.stringify({
        operation,
        run_id: runId,
        delta_ms: deltaMs,
      }),
    })
  ), []);

  const confirmPrepared = useCallback(async (token, operation) => {
    const result = await fetchJson(`/api/confirmations/${token}`, {
      method: "POST",
      body: "{}",
    });
    if (operation !== "shutdown") await refreshHistory();
    return result;
  }, [refreshHistory]);

  const setChartDays = useCallback(async (period) => {
    if (!VALID_PERIODS.includes(period)) return;
    setChartDaysState(period);
    localStorage.setItem("takt-chart-days", period);
    try {
      await refreshHistory(period);
    } catch {
      // Connection status communicates the unavailable server.
    }
  }, [refreshHistory]);

  return {
    state,
    history,
    system,
    connection,
    chartDays,
    setChartDays,
    sendAction,
    sendAudioRequest,
    prepareConfirmation,
    confirmPrepared,
  };
}
