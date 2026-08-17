// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import App from "./AppView";

function statePayload(revision: number) {
  return {
    state: "ready",
    state_label: "BEREIT",
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

function bootstrapPayload(revision: number) {
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
      model: "Browser",
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
        bluetooth_available: true,
        sound: "TAKT Startsignal",
        devices: [],
      },
    },
  };
}

type Listener = (event: Event | { data: string }) => void;

// Mirrors useTaktServer.test.tsx's fake — AppView's settings modal and
// connection status render regardless of whether the socket ever opens, so
// this smoke test never needs to open it.
class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: FakeWebSocket[] = [];

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

  send() {}

  close() {
    if (this.readyState >= FakeWebSocket.CLOSING) return;
    this.readyState = FakeWebSocket.CLOSED;
  }
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
}

describe("AppView (shared/ui migration smoke test)", () => {
  let root: Root | null = null;
  let container: HTMLDivElement | null = null;
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
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(async () => {
    if (root) {
      await act(async () => {
        root?.unmount();
        await flush();
      });
      root = null;
    }
    container?.remove();
    container = null;
    fetchMock.mockRestore();
    vi.useRealTimers();
  });

  async function mount() {
    root = createRoot(container as HTMLDivElement);
    await act(async () => {
      root?.render(createElement(App));
      await flush();
    });
  }

  it("renders the ready state and opens/closes settings via the migrated icon buttons", async () => {
    await mount();
    expect(document.querySelector(".timer-hit-area")).not.toBeNull();

    const settingsButton = document.querySelector(".settings-trigger") as HTMLButtonElement;
    expect(settingsButton).not.toBeNull();
    await act(async () => {
      settingsButton.click();
      await flush();
    });

    const modal = document.querySelector(".settings-modal");
    expect(modal).not.toBeNull();

    const closeButton = modal?.querySelector("header button") as HTMLButtonElement;
    expect(closeButton).not.toBeNull();
    await act(async () => {
      closeButton.click();
      await flush();
    });
    expect(document.querySelector(".settings-modal")).toBeNull();
  });

  it("toggles the migrated chart period-switch buttons", async () => {
    await mount();
    const buttons = Array.from(document.querySelectorAll(".period-switch button")) as HTMLButtonElement[];
    expect(buttons).toHaveLength(4);
    const ninetyDays = buttons.find((button) => button.textContent === "90 TAGE");
    expect(ninetyDays).toBeDefined();
    expect(ninetyDays?.getAttribute("aria-pressed")).toBe("false");

    await act(async () => {
      ninetyDays?.click();
      await flush();
    });
    expect(ninetyDays?.getAttribute("aria-pressed")).toBe("true");
  });

  it("opens the audio settings and shows the migrated bluetooth mode toggle", async () => {
    await mount();
    const settingsButton = document.querySelector(".settings-trigger") as HTMLButtonElement;
    await act(async () => {
      settingsButton.click();
      await flush();
    });

    const bluetoothToggle = Array.from(document.querySelectorAll(".audio-mode-switch button")).find(
      (button) => button.textContent?.includes("BLUETOOTH"),
    ) as HTMLButtonElement | undefined;
    expect(bluetoothToggle).toBeDefined();

    await act(async () => {
      bluetoothToggle?.click();
      await flush();
    });

    const select = document.querySelector(".bluetooth-select");
    expect(select).not.toBeNull();
    expect(select?.getAttribute("role")).toBe("combobox");
  });
});
