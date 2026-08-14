import { useCallback, useEffect, useRef, useState } from "react";
import {
  type BootstrapPayload,
  type ConfirmationPayload,
  type ConfirmationResponse,
  type HistoryPayload,
  type PiEvent,
  type SystemPayload,
  type TimerStatePayload,
  parseAction,
  parseBootstrap,
  parseConfirmation,
  parseConfirmationResponse,
  parseHistory,
  parsePiEvent,
  parseSystemResponse,
} from "./shared/contracts";
import { requestJson } from "./shared/httpClient";

const EMPTY_STATE: TimerStatePayload = {
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
  start_sequence: { active: false, phase: null, remaining_ms: 0, error: null },
  maintenance: { held: false, reason: null, expires_in_seconds: null },
};
const EMPTY_HISTORY: HistoryPayload = {
  today: [],
  today_count: 0,
  best: [],
  chart: [],
  all: [],
  chart_days: 30,
};
const EMPTY_SYSTEM: SystemPayload = {
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
const VALID_PERIODS = ["7", "30", "90", "all"] as const;
export type ChartPeriod = (typeof VALID_PERIODS)[number];

function isFileMode(): boolean {
  return window.location.protocol === "file:";
}
function schedule(callback: () => void): number {
  return window.setTimeout(callback, 1800);
}
export interface TaktServerState {
  state: TimerStatePayload;
  history: HistoryPayload;
  system: SystemPayload;
  connection: "offline" | "connecting" | "online";
  chartDays: ChartPeriod;
  setChartDays: (period: string) => Promise<void>;
  sendAction: (action: string) => Promise<ReturnType<typeof parseAction>>;
  sendAudioRequest: (
    operation: string,
    payload?: Record<string, unknown>,
  ) => Promise<ReturnType<typeof parseSystemResponse>>;
  prepareConfirmation: (
    operation: string,
    runId?: number | null,
    deltaMs?: number,
  ) => Promise<ConfirmationPayload>;
  confirmPrepared: (
    token: string,
    operation: string,
  ) => Promise<ConfirmationResponse>;
}

export function useTaktServer(): TaktServerState {
  const storedPeriod = window.localStorage.getItem("takt-chart-days");
  const initialPeriod: ChartPeriod = VALID_PERIODS.includes(
    storedPeriod as ChartPeriod,
  )
    ? (storedPeriod as ChartPeriod)
    : "30";
  const [chartDays, setChartDaysState] = useState<ChartPeriod>(initialPeriod);
  const [state, setState] = useState<TimerStatePayload>(EMPTY_STATE);
  const [history, setHistory] = useState<HistoryPayload>(EMPTY_HISTORY);
  const [system, setSystem] = useState<SystemPayload>(EMPTY_SYSTEM);
  const [connection, setConnection] = useState<TaktServerState["connection"]>(
    isFileMode() ? "offline" : "connecting",
  );
  const historyRevision = useRef(-1);
  const socketRef = useRef<WebSocket | null>(null);
  const connectRef = useRef<(() => void) | null>(null);
  const bootstrapRef = useRef<(() => Promise<void>) | null>(null);
  const retryRef = useRef<number | null>(null);
  const mountedRef = useRef(true);
  const refreshHistory = useCallback(
    async (period: ChartPeriod = chartDays) => {
      if (isFileMode()) return;
      const nextHistory = await requestJson(
        `/api/history?days=${period}`,
        {},
        parseHistory,
      );
      if (mountedRef.current) setHistory(nextHistory);
    },
    [chartDays],
  );
  const connectSocket = useCallback(() => {
    if (isFileMode() || !mountedRef.current) return;
    if (socketRef.current && socketRef.current.readyState < WebSocket.CLOSING)
      return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(
      `${protocol}//${window.location.host}/api/events`,
    );
    socketRef.current = socket;
    setConnection("connecting");
    socket.addEventListener("open", () => {
      if (mountedRef.current) setConnection("online");
    });
    socket.addEventListener("message", async (event: MessageEvent<string>) => {
      if (event.data === "pong" || !mountedRef.current) return;
      let message: PiEvent;
      try {
        message = parsePiEvent(JSON.parse(event.data) as unknown);
      } catch {
        return;
      }
      if (message.type === "state") {
        const changed =
          historyRevision.current >= 0 &&
          historyRevision.current !== message.data.history_revision;
        historyRevision.current = message.data.history_revision;
        setState(message.data);
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
      } else if (message.type === "system") setSystem(message.data);
    });
    socket.addEventListener("close", () => {
      if (!mountedRef.current) return;
      setConnection("offline");
      if (retryRef.current !== null) window.clearTimeout(retryRef.current);
      retryRef.current = schedule(() => connectRef.current?.());
    });
    socket.addEventListener("error", () => socket.close());
  }, [refreshHistory]);
  const bootstrap = useCallback(async () => {
    if (isFileMode()) return;
    try {
      const data: BootstrapPayload = await requestJson(
        `/api/bootstrap?days=${chartDays}`,
        {},
        parseBootstrap,
      );
      if (!mountedRef.current) return;
      historyRevision.current = data.state.history_revision;
      setState(data.state);
      setHistory(data.history);
      setSystem(data.system);
      connectSocket();
    } catch {
      if (!mountedRef.current) return;
      setConnection("offline");
      if (retryRef.current !== null) window.clearTimeout(retryRef.current);
      retryRef.current = schedule(() => bootstrapRef.current?.());
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
      if (retryRef.current !== null) window.clearTimeout(retryRef.current);
      socketRef.current?.close();
    };
  }, [bootstrap]);
  const sendAction = useCallback(async (action: string) => {
    const result = await requestJson(
      "/api/action",
      { method: "POST", body: { action } },
      parseAction,
    );
    setState(result.state);
    return result;
  }, []);
  const sendAudioRequest = useCallback(
    async (operation: string, payload: Record<string, unknown> = {}) => {
      const result = await requestJson(
        `/api/audio/${operation}`,
        { method: "POST", body: payload },
        parseSystemResponse,
      );
      setSystem(result.system);
      return result;
    },
    [],
  );
  const prepareConfirmation = useCallback(
    async (operation: string, runId: number | null = null, deltaMs = 0) =>
      requestJson(
        "/api/confirmations",
        {
          method: "POST",
          body: { operation, run_id: runId, delta_ms: deltaMs },
        },
        parseConfirmation,
      ),
    [],
  );
  const confirmPrepared = useCallback(
    async (token: string, operation: string) => {
      const result = await requestJson(
        `/api/confirmations/${token}`,
        { method: "POST", body: {} },
        parseConfirmationResponse,
      );
      if (operation !== "shutdown") await refreshHistory();
      return result;
    },
    [refreshHistory],
  );
  const setChartDays = useCallback(
    async (period: string) => {
      if (!VALID_PERIODS.includes(period as ChartPeriod)) return;
      const next = period as ChartPeriod;
      setChartDaysState(next);
      window.localStorage.setItem("takt-chart-days", next);
      try {
        await refreshHistory(next);
      } catch {
        /* connection state communicates unavailability */
      }
    },
    [refreshHistory],
  );
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
