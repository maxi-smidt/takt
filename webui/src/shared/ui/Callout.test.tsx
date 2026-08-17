// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Callout } from "./Callout";

async function flush(times = 5) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
}

describe("Callout", () => {
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

  it("marks a danger callout as an alert for screen readers", async () => {
    await act(async () => {
      root?.render(createElement(Callout, { tone: "danger", children: "This Pi is not idle." }));
      await flush();
    });
    const alert = container?.querySelector("[role='alert']");
    expect(alert?.textContent).toContain("This Pi is not idle.");
  });

  it("does not mark an info callout as an alert", async () => {
    await act(async () => {
      root?.render(createElement(Callout, { tone: "info", children: "Heads up." }));
      await flush();
    });
    expect(container?.querySelector("[role='alert']")).toBeNull();
  });
});
