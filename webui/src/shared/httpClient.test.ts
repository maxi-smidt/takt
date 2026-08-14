import { describe, expect, it, vi } from "vitest";
import { ApiError, requestJson } from "./httpClient";

describe("requestJson", () => {
  it("serializes JSON bodies and applies CSRF without touching multipart", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response('{"ok":true}', { status: 200 }));
    await requestJson(
      "/api/action",
      { method: "POST", body: { action: "primary" }, csrf: "token" },
      (value) => value as { ok: boolean },
    );
    const [, init] = fetchMock.mock.calls[0]!;
    expect((init?.headers as Headers).get("Content-Type")).toBe(
      "application/json",
    );
    expect((init?.headers as Headers).get("X-CSRF-Token")).toBe("token");
    expect(init?.body).toBe(JSON.stringify({ action: "primary" }));
    fetchMock.mockRestore();

    const stringBodyFetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("{}", { status: 200 }));
    await requestJson("/api/session", {
      method: "POST",
      body: JSON.stringify({ password: "secret" }),
    });
    const [, stringBodyInit] = stringBodyFetch.mock.calls[0]!;
    expect((stringBodyInit?.headers as Headers).get("Content-Type")).toBe(
      "application/json",
    );
    stringBodyFetch.mockRestore();

    const multipart = new FormData();
    multipart.append("artifact", new Blob(["release"]), "release.tar.gz");
    const multipartFetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("{}", { status: 200 }));
    await requestJson("/api/releases", { method: "POST", body: multipart });
    const [, multipartInit] = multipartFetch.mock.calls[0]!;
    expect(multipartInit?.body).toBe(multipart);
    expect((multipartInit?.headers as Headers).has("Content-Type")).toBe(false);
    multipartFetch.mockRestore();
  });

  it("preserves plain-text HTTP errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("not authorized", { status: 403 }),
    );
    await expect(requestJson("/api/session")).rejects.toEqual(
      expect.objectContaining({
        status: 403,
        message: "not authorized",
      } satisfies Partial<ApiError>),
    );
    vi.restoreAllMocks();
  });

  it("rejects invalid successful payloads through the parser", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("{}", { status: 200 }),
    );
    await expect(
      requestJson("/api/state", {}, () => {
        throw new Error("invalid state");
      }),
    ).rejects.toThrow("invalid state");
    vi.restoreAllMocks();
  });
});
