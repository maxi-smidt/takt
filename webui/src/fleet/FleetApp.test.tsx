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
