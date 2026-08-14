// @vitest-environment jsdom

import { act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { useTaktServer, type TaktServerState } from "./useTaktServer";

const HEARTBEAT_INTERVAL_MS = 20_000;
const LIVENESS_TIMEOUT_MS = 45_000;
const CONNECT_TIMEOUT_MS = 15_000;

function statePayload(revision: number, label = "BEREIT") {
  return {
    state: "ready",
    state_label: label,
    actual_ms: 0,
    actual: "00:00.00",
    added_ms: 0,
    added: "+00:00.00",
    total_ms: 0,
    total: "00:00.00",
    error: null,
    hardware: { label: "Browser", available: true },
    history_revision: revision,
    signal_revision: 0,
    signal: null,
    sound_playing: false,
    start_sequence: { active: false, phase: null, remaining_ms: 0, error: null },
    maintenance: { held: false, reason: null, expires_in_seconds: null },
  };
}

function bootstrapPayload(revision: number, model = "Browser") {
  return {
    state: statePayload(revision),
    history: {
      today: [],
      today_count: 0,
      best: [],
      chart: [],
      all: [],
      chart_days: 30,
    },
    system: {
      shutdown_available: false,
      model,
      mock_button: false,
      mock_buzzer: false,
      audio: {
        enabled: false,
        output: "off",
        delay_milliseconds: 3000,
        clip_duration_milliseconds: 0,
        device_address: null,
        device_name: null,
        playback_available: false,
        bluetooth_available: false,
        sound: "TAKT Startsignal",
        devices: [],
      },
    },
  };
}

type Listener = (event: Event | { data: string }) => void;

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readonly sent: string[] = [];
  readonly listeners = new Map<string, Set<Listener>>();
  readyState = FakeWebSocket.CONNECTING;

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  addEventListener(type: string, listener: Listener) {
    const listeners = this.listeners.get(type) ?? new Set<Listener>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: Listener) {
    this.listeners.get(type)?.delete(listener);
  }

  send(value: string) {
    this.sent.push(value);
  }

  close() {
    if (this.readyState >= FakeWebSocket.CLOSING) return;
    this.readyState = FakeWebSocket.CLOSED;
    this.dispatch("close", new Event("close"));
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.dispatch("open", new Event("open"));
  }

  message(data: string) {
    this.dispatch("message", { data });
  }

  private dispatch(type: string, event: Event | { data: string }) {
    this.listeners.get(type)?.forEach((listener) => listener(event));
  }
}

function Probe({ onState }: { onState: (state: TaktServerState) => void }): ReactNode {
  onState(useTaktServer());
  return null;
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
}

async function openLatestSocket() {
  const socket = FakeWebSocket.instances.at(-1);
  if (!socket) throw new Error("Expected a WebSocket instance.");
  await act(async () => {
    socket.open();
    await flush();
  });
  return socket;
}

describe("useTaktServer connection recovery", () => {
  let root: Root | null = null;
  let latest: TaktServerState;
  let fetchMock: MockInstance<typeof fetch>;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    Object.defineProperty(globalThis, "WebSocket", {
      configurable: true,
      writable: true,
      value: FakeWebSocket,
    });
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify(bootstrapPayload(1)), { status: 200 }),
    );
  });

  afterEach(async () => {
    if (root) {
      await act(async () => {
        root?.unmount();
        await flush();
      });
      root = null;
    }
    fetchMock.mockRestore();
    vi.useRealTimers();
  });

  async function mountWithoutOpening() {
    root = createRoot(document.createElement("div"));
    await act(async () => {
      root?.render(
        createElement(Probe, {
          onState: (state) => {
            latest = state;
          },
        }),
      );
      await flush();
    });
    const socket = FakeWebSocket.instances[0];
    if (!socket) throw new Error("Expected the initial WebSocket instance.");
    return socket;
  }

  async function mount() {
    root = createRoot(document.createElement("div"));
    await act(async () => {
      root?.render(
        createElement(Probe, {
          onState: (state) => {
            latest = state;
          },
        }),
      );
      await flush();
    });
    return openLatestSocket();
  }

  it("loads bootstrap data and retries a handshake that never opens", async () => {
    const socket = await mountWithoutOpening();
    expect(latest.system.model).toBe("Browser");
    expect(latest.connection).toBe("connecting");

    await act(async () => {
      vi.advanceTimersByTime(CONNECT_TIMEOUT_MS);
      await flush();
    });
    expect(socket.readyState).toBe(FakeWebSocket.CLOSED);
    expect(latest.connection).toBe("offline");

    await act(async () => {
      vi.advanceTimersByTime(2_000);
      await flush();
    });

    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("detects a silent open socket and sends application-level heartbeats", async () => {
    const socket = await mount();
    expect(latest.connection).toBe("online");

    await act(async () => {
      vi.advanceTimersByTime(HEARTBEAT_INTERVAL_MS);
      await flush();
    });
    expect(socket.sent).toContain("ping");

    socket.message("pong");
    await act(async () => {
      vi.advanceTimersByTime(LIVENESS_TIMEOUT_MS - 1);
      await flush();
    });
    expect(socket.readyState).toBe(FakeWebSocket.OPEN);

    await act(async () => {
      vi.advanceTimersByTime(2);
      await flush();
    });
    expect(socket.readyState).toBe(FakeWebSocket.CLOSED);
    expect(latest.connection).toBe("offline");
  });

  it("keeps live sockets during lifecycle wakeups and recovers dead sockets", async () => {
    const socket = await mount();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    Object.defineProperty(document, "visibilityState", { value: "hidden" });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await flush();
    });
    expect(FakeWebSocket.instances).toHaveLength(1);
    Object.defineProperty(document, "visibilityState", { value: "visible" });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await flush();
    });
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const pageShow = new Event("pageshow");
    Object.defineProperty(pageShow, "persisted", { value: true });
    await act(async () => {
      window.dispatchEvent(pageShow);
      await flush();
    });
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => {
      socket.close();
      await flush();
    });
    expect(latest.connection).toBe("offline");
    await act(async () => {
      window.dispatchEvent(new Event("online"));
      await flush();
    });
    const recoveredSocket = await openLatestSocket();
    expect(recoveredSocket).not.toBe(socket);
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(latest.connection).toBe("online");
  });

  it("keeps live events when bootstrap resync fails", async () => {
    fetchMock.mockImplementationOnce(async () =>
      new Response(JSON.stringify(bootstrapPayload(1)), { status: 200 }),
    );
    fetchMock.mockImplementationOnce(async () =>
      new Response("unavailable", { status: 503 }),
    );
    const socket = await mount();
    expect(socket.readyState).toBe(FakeWebSocket.OPEN);
    expect(latest.connection).toBe("online");
    await act(async () => {
      socket.message(
        JSON.stringify({ type: "state", data: statePayload(2, "LIVE") }),
      );
      await flush();
    });
    expect(latest.state.state_label).toBe("LIVE");
  });

  it("applies the newest event received while bootstrap is pending", async () => {
    let resolveBootstrap: ((response: Response) => void) | undefined;
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveBootstrap = resolve;
        }),
    );
    root = createRoot(document.createElement("div"));
    await act(async () => {
      root?.render(
        createElement(Probe, {
          onState: (state) => {
            latest = state;
          },
        }),
      );
      await flush();
    });
    const socket = FakeWebSocket.instances[0];
    if (!socket) throw new Error("Expected the initial WebSocket instance.");
    await act(async () => {
      socket.open();
      socket.message(JSON.stringify({ type: "state", data: statePayload(2, "NEU") }));
      resolveBootstrap?.(
        new Response(JSON.stringify(bootstrapPayload(1)), { status: 200 }),
      );
      await flush();
    });
    expect(latest.state.state_label).toBe("NEU");
    expect(latest.connection).toBe("online");
  });
});
