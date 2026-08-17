// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import App from "../FleetApp";

async function flush(times = 25) {
  for (let i = 0; i < times; i += 1) {
    await Promise.resolve();
  }
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

const AUTHENTICATED_PORTAL_USER = {
  authenticated: true,
  csrf_token: "csrf-token",
  user: { id: "u1", username: "spotter", is_admin: false, must_change_password: false },
};

function runsPayloadFor(deviceId: string) {
  const runNumber = deviceId === "d1" ? 1 : 2;
  return {
    device: { id: deviceId, name: deviceId },
    mirror: { sha256: "abc", last_mirrored_at: "2026-08-10T10:00:00Z", state: "fresh" },
    summary: { count: 1, best_total_ms: 12345, average_actual_ms: 12000, average_total_ms: 12345 },
    runs: [
      {
        id: `${deviceId}-run-1`,
        run_number: runNumber,
        session_date: "2026-08-10",
        started_at: "2026-08-10T10:00:00Z",
        stopped_at: "2026-08-10T10:01:00Z",
        saved_at: "2026-08-10T10:01:00Z",
        actual_time_ms: 12000,
        added_time_ms: 0,
        total_time_ms: 12345,
        updated_at: "2026-08-10T10:01:00Z",
      },
    ],
    next_cursor: null,
  };
}

describe("Portal device selection", () => {
  let root: Root | null = null;
  let container: HTMLDivElement | null = null;
  let fetchMock: MockInstance<typeof fetch>;

  beforeEach(() => {
    (
      globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
    ).IS_REACT_ACT_ENVIRONMENT = true;
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
  });

  function findDeviceCard(name: string) {
    const cards = Array.from(document.querySelectorAll(".portal-device"));
    const card = cards.find((candidate) => candidate.textContent?.includes(name));
    if (!card) throw new Error(`Device card not found: ${name}`);
    return card as HTMLButtonElement;
  }

  it("switches the active device, its runs, and the visible selection when a second device is picked", async () => {
    const runsCalls: string[] = [];
    fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url.endsWith("/api/session")) return jsonResponse(AUTHENTICATED_PORTAL_USER);
      if (url.endsWith("/api/portal/devices")) {
        return jsonResponse({
          devices: [
            { id: "d1", name: "Bahn 1", hostname: "d1.local", online: true, access: "write", run_count: 4, last_mirrored_at: "2026-08-10T10:00:00Z", mirror_state: "fresh" },
            { id: "d2", name: "Bahn 2", hostname: "d2.local", online: true, access: "write", run_count: 2, last_mirrored_at: "2026-08-09T10:00:00Z", mirror_state: "fresh" },
          ],
        });
      }
      const runsMatch = url.match(/\/api\/portal\/devices\/(d1|d2)\/runs/);
      if (runsMatch) {
        const deviceId = runsMatch[1]!;
        runsCalls.push(deviceId);
        return jsonResponse(runsPayloadFor(deviceId));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root?.render(createElement(App));
      await flush();
    });

    // The first device is selected by default.
    expect(findDeviceCard("Bahn 1").className).toContain("selected");
    expect(findDeviceCard("Bahn 2").className).not.toContain("selected");
    expect(runsCalls).toContain("d1");
    expect(document.querySelector(".runs-table")?.textContent).toContain("1");

    await act(async () => {
      findDeviceCard("Bahn 2").dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(findDeviceCard("Bahn 2").className).toContain("selected");
    expect(findDeviceCard("Bahn 1").className).not.toContain("selected");
    expect(runsCalls).toContain("d2");
    expect(document.querySelector(".runs-table")?.textContent).toContain("2");
  });
});
