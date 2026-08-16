// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import App from "./FleetApp";

async function flush(times = 25) {
  for (let i = 0; i < times; i += 1) {
    await Promise.resolve();
  }
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

const AUTHENTICATED_ADMIN = {
  authenticated: true,
  csrf_token: "csrf-token",
  user: { id: "u1", username: "admin", is_admin: true, must_change_password: false },
};

describe("FleetApp reload session handling", () => {
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

  async function mount() {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root?.render(createElement(App));
      await flush();
    });
  }

  it("reloading with a still-valid session shows the dashboard without a login error", async () => {
    fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      const method = (init?.method ?? "GET").toUpperCase();
      if (method === "GET" && url.endsWith("/api/session")) return jsonResponse(AUTHENTICATED_ADMIN);
      if (method === "GET" && url.endsWith("/api/devices")) return jsonResponse({ devices: [] });
      if (method === "GET" && url.endsWith("/api/releases"))
        return jsonResponse({ releases: [], bundled_release: null });
      if (method === "GET" && url.endsWith("/api/jobs")) return jsonResponse({ jobs: [] });
      if (method === "GET" && url.endsWith("/api/admin/users")) return jsonResponse({ users: [] });
      throw new Error(`Unexpected fetch: ${method} ${url}`);
    });

    await mount();

    expect(container?.textContent).toContain("DEVICE REGISTRY");
    expect(container?.textContent).not.toContain("Login required");
    expect(container?.textContent).not.toContain("Administrator login required");
  });

  it("reloading with an expired session redirects to the login screen instead of showing an error banner", async () => {
    let sessionCalls = 0;
    fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      const method = (init?.method ?? "GET").toUpperCase();
      if (method === "GET" && url.endsWith("/api/session")) {
        sessionCalls += 1;
        // The very first probe still sees a session (a page reload sends the
        // cookie before it is actually revoked/expired server-side); the
        // dashboard's own data request is what discovers it is really dead.
        return jsonResponse(sessionCalls === 1 ? AUTHENTICATED_ADMIN : { authenticated: false });
      }
      if (method === "GET" && url.endsWith("/api/devices"))
        return new Response("Login required.", { status: 401 });
      if (method === "GET" && url.endsWith("/api/releases"))
        return jsonResponse({ releases: [], bundled_release: null });
      if (method === "GET" && url.endsWith("/api/jobs")) return jsonResponse({ jobs: [] });
      if (method === "GET" && url.endsWith("/api/admin/users")) return jsonResponse({ users: [] });
      throw new Error(`Unexpected fetch: ${method} ${url}`);
    });

    await mount();
    await act(async () => {
      await flush();
    });

    expect(container?.textContent).toContain("OPEN REGISTRY");
    expect(container?.textContent).not.toContain("DEVICE REGISTRY");
    expect(container?.textContent).not.toContain("Login required");
    expect(container?.textContent).not.toContain("Administrator login required");
  });
});

describe("UserAdminPanel device access", () => {
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

  function clickButton(text: string, scope: ParentNode = document) {
    const button = Array.from(scope.querySelectorAll("button")).find(
      (candidate) => candidate.textContent?.trim() === text,
    );
    if (!button) throw new Error(`Button not found: ${text}`);
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  }

  function setSelectValue(select: HTMLSelectElement, value: string) {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLSelectElement.prototype,
      "value",
    )?.set;
    setter?.call(select, value);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function findAccessRow(deviceName: string) {
    const rows = Array.from(document.querySelectorAll(".access-row"));
    const row = rows.find((candidate) => candidate.textContent?.includes(deviceName));
    if (!row) throw new Error(`Access row not found for ${deviceName}`);
    return row.querySelector("select") as HTMLSelectElement;
  }

  it("grants several devices, changes an access level, and revokes access", async () => {
    const devices = [
      { id: "d1", name: "Bahn 1" },
      { id: "d2", name: "Bahn 2" },
    ];
    let access: { device_id: string; access_level: string }[] = [];
    const grantCalls: { deviceId: string; access: string }[] = [];
    const revokeCalls: string[] = [];

    fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      const method = (init?.method ?? "GET").toUpperCase();
      if (method === "GET" && url.endsWith("/api/session")) return jsonResponse(AUTHENTICATED_ADMIN);
      if (method === "GET" && url.endsWith("/api/devices"))
        return jsonResponse({
          devices: devices.map((device) => ({
            id: device.id,
            name: device.name,
            hostname: `${device.id}.local`,
            online: false,
          })),
        });
      if (method === "GET" && url.endsWith("/api/releases"))
        return jsonResponse({ releases: [], bundled_release: null });
      if (method === "GET" && url.endsWith("/api/jobs")) return jsonResponse({ jobs: [] });
      if (method === "GET" && url.endsWith("/api/admin/users"))
        return jsonResponse({
          users: [{ id: "u2", username: "operator", is_admin: false, disabled: false, access }],
        });
      const grantMatch = url.match(/\/api\/admin\/users\/u2\/devices\/(d1|d2)$/);
      if (grantMatch && method === "PUT") {
        const deviceId = grantMatch[1];
        const body = JSON.parse((init?.body as string) ?? "{}");
        grantCalls.push({ deviceId, access: body.access });
        access = [...access.filter((item) => item.device_id !== deviceId), { device_id: deviceId, access_level: body.access }];
        return jsonResponse({ access: { user_id: "u2", device_id: deviceId, access_level: body.access } });
      }
      if (grantMatch && method === "DELETE") {
        const deviceId = grantMatch[1];
        revokeCalls.push(deviceId);
        access = access.filter((item) => item.device_id !== deviceId);
        return jsonResponse({ ok: true });
      }
      throw new Error(`Unexpected fetch: ${method} ${url}`);
    });

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root?.render(createElement(App));
      await flush();
    });

    await act(async () => {
      clickButton("MANAGE ACCESS", container as HTMLDivElement);
      await flush();
    });

    // Grant read access to Bahn 1 and write access to Bahn 2.
    await act(async () => {
      setSelectValue(findAccessRow("Bahn 1"), "read");
      await flush();
    });
    await act(async () => {
      setSelectValue(findAccessRow("Bahn 2"), "write");
      await flush();
    });

    const summaryText = () => document.querySelector(".access-summary")?.textContent ?? "";

    expect(grantCalls).toContainEqual({ deviceId: "d1", access: "read" });
    expect(grantCalls).toContainEqual({ deviceId: "d2", access: "write" });
    expect(summaryText()).toContain("Bahn 1 · READ");
    expect(summaryText()).toContain("Bahn 2 · WRITE");

    // Change Bahn 1's level from read to write.
    await act(async () => {
      setSelectValue(findAccessRow("Bahn 1"), "write");
      await flush();
    });
    expect(grantCalls).toContainEqual({ deviceId: "d1", access: "write" });
    expect(summaryText()).toContain("Bahn 1 · WRITE");

    // Revoke Bahn 2 entirely.
    await act(async () => {
      setSelectValue(findAccessRow("Bahn 2"), "none");
      await flush();
    });
    expect(revokeCalls).toContain("d2");
    expect(summaryText()).not.toContain("Bahn 2");
    expect(summaryText()).toContain("Bahn 1 · WRITE");
  });
});
