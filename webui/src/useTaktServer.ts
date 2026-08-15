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
import { requestBlob, requestJson, withTimeout } from "./shared/httpClient";

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
export type DataExportFormat = "db" | "csv";

const HEARTBEAT_INTERVAL_MS = 20_000;
const LIVENESS_TIMEOUT_MS = 45_000;
const CONNECT_TIMEOUT_MS = 15_000;
const BOOTSTRAP_TIMEOUT_MS = 10_000;
const RETRY_BASE_MS = 1_000;
const RETRY_MAX_MS = 30_000;
const RETRY_JITTER_MIN = 0.8;
const RETRY_JITTER_MAX = 1.2;
const MUTATION_TIMEOUT_MS = 15_000;

interface PendingEvents {
  state: TimerStatePayload | null;
  system: SystemPayload | null;
  historyChanged: boolean;
}

type ExclusiveRequest = "action" | "audio" | "confirmation" | "export";

type PendingRequests = Record<ExclusiveRequest, boolean>;
type RequestMap<T> = Map<string, Promise<T>>;

function isFileMode(): boolean {
  return window.location.protocol === "file:";
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
  downloadExport: (format: DataExportFormat) => Promise<string>;
  confirmPrepared: (
    token: string,
    operation: string,
  ) => Promise<ConfirmationResponse>;
  pending: PendingRequests;
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
  const chartDaysRef = useRef(chartDays);
  const historyRevision = useRef(-1);
  const socketRef = useRef<WebSocket | null>(null);
  const connectRef = useRef<(() => void) | null>(null);
  const recoverRef = useRef<(() => void) | null>(null);
  const retryRef = useRef<number | null>(null);
  const retryAttemptRef = useRef(0);
  const heartbeatRef = useRef<number | null>(null);
  const livenessRef = useRef<number | null>(null);
  const connectTimeoutRef = useRef<number | null>(null);
  const bootstrapAbortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const historyRequestRef = useRef(0);
  const refreshHistory = useCallback(
    async (period: ChartPeriod = chartDaysRef.current) => {
      if (isFileMode()) return;
      const requestId = ++historyRequestRef.current;
      const nextHistory = await requestJson(
        `/api/history?days=${period}`,
        {},
        parseHistory,
      );
      if (mountedRef.current && requestId === historyRequestRef.current) {
        setHistory(nextHistory);
      }
    },
    [],
  );
  const clearRetry = useCallback(() => {
    if (retryRef.current !== null) {
      window.clearTimeout(retryRef.current);
      retryRef.current = null;
    }
  }, []);
  const pendingCountsRef = useRef<Record<ExclusiveRequest, number>>({
    action: 0,
    audio: 0,
    confirmation: 0,
    export: 0,
  });
  const actionRequestsRef = useRef<RequestMap<ReturnType<typeof parseAction>>>(new Map());
  const audioRequestsRef = useRef<RequestMap<ReturnType<typeof parseSystemResponse>>>(new Map());
  const prepareRequestsRef = useRef<RequestMap<ConfirmationPayload>>(new Map());
  const confirmRequestsRef = useRef<RequestMap<ConfirmationResponse>>(new Map());
  const exportRequestsRef = useRef<RequestMap<string>>(new Map());
  const [pending, setPending] = useState<PendingRequests>({
    action: false,
    audio: false,
    confirmation: false,
    export: false,
  });
  const runExclusive = useCallback(
    <T,>(
      requests: RequestMap<T>,
      requestKey: string,
      pendingKey: ExclusiveRequest,
      task: () => Promise<T>,
    ): Promise<T> => {
      const existing = requests.get(requestKey);
      if (existing) return existing;

      pendingCountsRef.current[pendingKey] += 1;
      setPending((current) => ({ ...current, [pendingKey]: true }));
      const request = task();
      requests.set(requestKey, request);
      const clear = () => {
        if (requests.get(requestKey) === request) {
          requests.delete(requestKey);
          pendingCountsRef.current[pendingKey] = Math.max(
            0,
            pendingCountsRef.current[pendingKey] - 1,
          );
          if (pendingCountsRef.current[pendingKey] === 0) {
            setPending((current) => ({ ...current, [pendingKey]: false }));
          }
        }
      };
      void request.then(clear, clear);
      return request;
    },
    [],
  );

  const scheduleRetry = useCallback(() => {
    if (!mountedRef.current || retryRef.current !== null) return;
    const attempt = retryAttemptRef.current;
    const delay = Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** attempt);
    const jitter =
      RETRY_JITTER_MIN +
      Math.random() * (RETRY_JITTER_MAX - RETRY_JITTER_MIN);
    retryAttemptRef.current += 1;
    retryRef.current = window.setTimeout(() => {
      retryRef.current = null;
      if (document.visibilityState === "visible") connectRef.current?.();
    }, delay * jitter);
  }, []);

  const stopSocket = useCallback((socket: WebSocket | null) => {
    if (heartbeatRef.current !== null) {
      window.clearInterval(heartbeatRef.current);
      heartbeatRef.current = null;
    }
    if (livenessRef.current !== null) {
      window.clearTimeout(livenessRef.current);
      livenessRef.current = null;
    }
    if (connectTimeoutRef.current !== null) {
      window.clearTimeout(connectTimeoutRef.current);
      connectTimeoutRef.current = null;
    }
    if (bootstrapAbortRef.current !== null) {
      bootstrapAbortRef.current.abort();
      bootstrapAbortRef.current = null;
    }
    if (socket && socket.readyState < WebSocket.CLOSING) socket.close();
  }, []);

  const applyEvent = useCallback(
    (message: PiEvent) => {
      if (message.type === "state") {
        const changed =
          historyRevision.current >= 0 &&
          historyRevision.current !== message.data.history_revision;
        historyRevision.current = message.data.history_revision;
        setState(message.data);
        if (changed) {
          void refreshHistory().catch(() => {
            // A later bootstrap will refresh the complete data set.
          });
        }
      } else if (message.type === "history_changed") {
        void refreshHistory().catch(() => {
          // A later bootstrap will refresh the complete data set.
        });
      } else if (message.type === "system") setSystem(message.data);
    },
    [refreshHistory],
  );

  const handleSocketFailure = useCallback(
    (socket: WebSocket) => {
      if (!mountedRef.current || socketRef.current !== socket) return;
      socketRef.current = null;
      stopSocket(socket);
      setConnection("offline");
      scheduleRetry();
    },
    [scheduleRetry, stopSocket],
  );

  const touchSocket = useCallback(
    (socket: WebSocket) => {
      if (!mountedRef.current || socketRef.current !== socket) return;
      if (livenessRef.current !== null) {
        window.clearTimeout(livenessRef.current);
        livenessRef.current = null;
      }
      if (document.visibilityState !== "visible") return;
      livenessRef.current = window.setTimeout(() => {
        livenessRef.current = null;
        if (
          document.visibilityState === "visible" &&
          socket.readyState === WebSocket.OPEN
        )
          handleSocketFailure(socket);
      }, LIVENESS_TIMEOUT_MS);
    },
    [handleSocketFailure],
  );

  const loadBootstrap = useCallback(async () => {
    if (isFileMode()) return;
    try {
      const data: BootstrapPayload = await requestJson(
        `/api/bootstrap?days=${chartDaysRef.current}`,
        { signal: withTimeout(undefined, BOOTSTRAP_TIMEOUT_MS) },
        parseBootstrap,
      );
      if (
        !mountedRef.current ||
        socketRef.current?.readyState === WebSocket.OPEN
      )
        return;
      historyRevision.current = data.state.history_revision;
      setState(data.state);
      setHistory(data.history);
      setSystem(data.system);
    } catch {
      // A live WebSocket or a later retry can still provide the current state.
    }
  }, []);

  const applyPendingEvents = useCallback(
    (pending: PendingEvents) => {
      const queuedState = pending.state;
      const queuedSystem = pending.system;
      const queuedHistoryChanged = pending.historyChanged;
      pending.state = null;
      pending.system = null;
      pending.historyChanged = false;
      if (queuedState) {
        const historyChanged =
          queuedState.history_revision !== historyRevision.current;
        historyRevision.current = queuedState.history_revision;
        setState(queuedState);
        if (historyChanged || queuedHistoryChanged) {
          void refreshHistory().catch(() => {
            // The next bootstrap will refresh the complete data set.
          });
        }
      } else if (queuedHistoryChanged) {
        void refreshHistory().catch(() => {
          // The next bootstrap will refresh the complete data set.
        });
      }
      if (queuedSystem) setSystem(queuedSystem);
    },
    [refreshHistory],
  );

  const resyncSocket = useCallback(
    async (socket: WebSocket, pending: PendingEvents) => {
      const controller = new AbortController();
      bootstrapAbortRef.current = controller;
      try {
        const data: BootstrapPayload = await requestJson(
          `/api/bootstrap?days=${chartDaysRef.current}`,
          { signal: withTimeout(controller.signal, BOOTSTRAP_TIMEOUT_MS) },
          parseBootstrap,
        );
        if (
          !mountedRef.current ||
          socketRef.current !== socket ||
          controller.signal.aborted
        )
          return;

        historyRevision.current = data.state.history_revision;
        setState(data.state);
        setHistory(data.history);
        setSystem(data.system);

        applyPendingEvents(pending);
        retryAttemptRef.current = 0;
        setConnection("online");
      } catch {
        if (
          mountedRef.current &&
          socketRef.current === socket &&
          socket.readyState === WebSocket.OPEN
        ) {
          applyPendingEvents(pending);
          retryAttemptRef.current = 0;
          setConnection("online");
        }
      } finally {
        if (bootstrapAbortRef.current === controller)
          bootstrapAbortRef.current = null;
      }
    },
    [applyPendingEvents],
  );

  const connectSocket = useCallback(() => {
    if (isFileMode() || !mountedRef.current || socketRef.current) return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    let socket: WebSocket;
    try {
      socket = new WebSocket(
        `${protocol}//${window.location.host}/api/events`,
      );
    } catch {
      setConnection("offline");
      scheduleRetry();
      return;
    }
    socketRef.current = socket;
    setConnection("connecting");
    connectTimeoutRef.current = window.setTimeout(() => {
      connectTimeoutRef.current = null;
      if (
        mountedRef.current &&
        socketRef.current === socket &&
        socket.readyState === WebSocket.CONNECTING
      )
        handleSocketFailure(socket);
    }, CONNECT_TIMEOUT_MS);
    const pending: PendingEvents = {
      state: null,
      system: null,
      historyChanged: false,
    };
    let syncing = true;
    socket.addEventListener("open", () => {
      if (!mountedRef.current || socketRef.current !== socket) return;
      if (connectTimeoutRef.current !== null) {
        window.clearTimeout(connectTimeoutRef.current);
        connectTimeoutRef.current = null;
      }
      touchSocket(socket);
      heartbeatRef.current = window.setInterval(() => {
        if (
          document.visibilityState === "visible" &&
          socket.readyState === WebSocket.OPEN
        ) {
          try {
            socket.send("ping");
          } catch {
            handleSocketFailure(socket);
          }
        }
      }, HEARTBEAT_INTERVAL_MS);
      void resyncSocket(socket, pending).then(() => {
        syncing = false;
      });
    });
    socket.addEventListener("message", (event: MessageEvent<string>) => {
      if (!mountedRef.current || socketRef.current !== socket) return;
      touchSocket(socket);
      if (event.data === "pong") return;
      let message: PiEvent;
      try {
        message = parsePiEvent(JSON.parse(event.data) as unknown);
      } catch (error) {
        console.warn("Ignoring invalid Pi WebSocket event.", error);
        return;
      }
      if (syncing) {
        if (message.type === "state") pending.state = message.data;
        else if (message.type === "system") pending.system = message.data;
        else pending.historyChanged = true;
      } else applyEvent(message);
    });
    socket.addEventListener("close", () => handleSocketFailure(socket));
    socket.addEventListener("error", () => handleSocketFailure(socket));
  }, [
    applyEvent,
    handleSocketFailure,
    resyncSocket,
    scheduleRetry,
    touchSocket,
  ]);

  const recoverNow = useCallback(() => {
    if (isFileMode() || !mountedRef.current) return;
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      touchSocket(socket);
      return;
    }
    clearRetry();
    socketRef.current = null;
    stopSocket(socket);
    setConnection("connecting");
    connectSocket();
  }, [clearRetry, connectSocket, stopSocket, touchSocket]);

  useEffect(() => {
    connectRef.current = connectSocket;
    recoverRef.current = recoverNow;
  }, [connectSocket, recoverNow]);
  useEffect(() => {
    mountedRef.current = true;
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") recoverRef.current?.();
    };
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) recoverRef.current?.();
    };
    const onOnline = () => recoverRef.current?.();
    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("pageshow", onPageShow);
    window.addEventListener("online", onOnline);
    queueMicrotask(() => {
      void loadBootstrap();
      recoverRef.current?.();
    });
    return () => {
      mountedRef.current = false;
      clearRetry();
      retryAttemptRef.current = 0;
      const socket = socketRef.current;
      socketRef.current = null;
      stopSocket(socket);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("pageshow", onPageShow);
      window.removeEventListener("online", onOnline);
    };
  }, [clearRetry, loadBootstrap, stopSocket]);
  const sendAction = useCallback(
    (action: string) =>
      runExclusive(
        actionRequestsRef.current,
        `action:${action}`,
        "action",
        async () => {
          const result = await requestJson(
            "/api/action",
            {
              method: "POST",
              body: { action },
              signal: withTimeout(undefined, MUTATION_TIMEOUT_MS),
            },
            parseAction,
          );
          setState(result.state);
          return result;
        },
      ),
    [runExclusive],
  );
  const sendAudioRequest = useCallback(
    (operation: string, payload: Record<string, unknown> = {}) =>
      runExclusive(
        audioRequestsRef.current,
        `audio:${operation}:${JSON.stringify(payload)}`,
        "audio",
        async () => {
          const result = await requestJson(
            `/api/audio/${operation}`,
            {
              method: "POST",
              body: payload,
              signal: withTimeout(undefined, MUTATION_TIMEOUT_MS),
            },
            parseSystemResponse,
          );
          setSystem(result.system);
          return result;
        },
      ),
    [runExclusive],
  );
  const prepareConfirmation = useCallback(
    (operation: string, runId: number | null = null, deltaMs = 0) =>
      runExclusive(
        prepareRequestsRef.current,
        `prepare:${operation}:${runId ?? ""}:${deltaMs}`,
        "confirmation",
        () =>
          requestJson(
            "/api/confirmations",
            {
              method: "POST",
              body: { operation, run_id: runId, delta_ms: deltaMs },
              signal: withTimeout(undefined, MUTATION_TIMEOUT_MS),
            },
            parseConfirmation,
          ),
      ),
    [runExclusive],
  );
  const confirmPrepared = useCallback(
    (token: string, operation: string) =>
      runExclusive(
        confirmRequestsRef.current,
        `confirm:${token}:${operation}`,
        "confirmation",
        async () => {
          const result = await requestJson(
            `/api/confirmations/${token}`,
            {
              method: "POST",
              body: {},
              signal: withTimeout(undefined, MUTATION_TIMEOUT_MS),
            },
            parseConfirmationResponse,
          );
          if (operation !== "shutdown") await refreshHistory();
          return result;
        },
      ),
    [refreshHistory, runExclusive],
  );
  const downloadExport = useCallback(
    (format: DataExportFormat) =>
      runExclusive(
        exportRequestsRef.current,
        `export:${format}`,
        "export",
        async () => {
          if (isFileMode()) throw new Error("Der Datenexport ist im Vorschaumodus nicht verfügbar.");
          const fallbackFilename = format === "db" ? "takt.db" : "takt-runs.csv";
          const result = await requestBlob(
            `/api/database/export?format=${format}`,
            fallbackFilename,
            { signal: withTimeout(undefined, MUTATION_TIMEOUT_MS) },
          );
          const objectUrl = URL.createObjectURL(result.blob);
          const link = document.createElement("a");
          link.href = objectUrl;
          link.download = result.filename;
          document.body.append(link);
          link.click();
          link.remove();
          window.setTimeout(() => URL.revokeObjectURL(objectUrl), 500);
          return result.filename;
        },
      ),
    [runExclusive],
  );

  const setChartDays = useCallback(
    async (period: string) => {
      if (!VALID_PERIODS.includes(period as ChartPeriod)) return;
      const next = period as ChartPeriod;
      setChartDaysState(next);
      chartDaysRef.current = next;
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
    downloadExport,
    confirmPrepared,
    pending,
  };
}
