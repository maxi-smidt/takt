// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Badge } from "./Badge";

async function flush(times = 5) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
}

describe("Badge", () => {
  let root: Root | null = null;
  let container: HTMLDivElement | null = null;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root?.unmount();
      await flush();
    });
    container?.remove();
    container = null;
    root = null;
  });

  it("defaults to the neutral tone with no tone class", async () => {
    await act(async () => {
      root?.render(createElement(Badge, null, "v0.5.1"));
      await flush();
    });
    const span = container?.querySelector("span");
    expect(span?.textContent).toBe("v0.5.1");
    expect(span?.className).toBe("takt-badge");
  });

  it("applies the requested tone class", async () => {
    await act(async () => {
      root?.render(createElement(Badge, { tone: "danger", children: "OFFLINE" }));
      await flush();
    });
    expect(container?.querySelector("span")?.className).toBe("takt-badge takt-badge-danger");
  });
});
