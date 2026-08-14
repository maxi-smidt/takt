import { afterEach, describe, expect, it, vi } from "vitest";
import { request } from "./fleetService";

describe("fleet request response routing", () => {
  afterEach(() => vi.restoreAllMocks());

  it("does not parse session mutations as session status", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response('{"ok":true}', { status: 200 }))
      .mockResolvedValueOnce(new Response('{"ok":true}', { status: 200 }));

    await expect(
      request("/api/session", {
        method: "POST",
        body: JSON.stringify({ password: "secret" }),
      }),
    ).resolves.toEqual({ ok: true });
    await expect(
      request("/api/session", { method: "DELETE" }, "csrf-token"),
    ).resolves.toEqual({ ok: true });
  });

  it("does not parse release uploads as the release list", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response('{"release":{"id":"new-release"}}', { status: 201 }),
    );

    await expect(
      request("/api/releases", { method: "POST", body: new FormData() }),
    ).resolves.toEqual({ release: { id: "new-release" } });
  });

  it("does not parse the deployment collection as one deployment", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response('{"deployments":[]}', { status: 200 }),
    );

    await expect(request("/api/deployments")).resolves.toEqual({
      deployments: [],
    });
  });
});
