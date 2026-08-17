// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Button } from "./Button";

async function flush(times = 5) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
}

describe("Button", () => {
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

  it("defaults to a secondary, type=button element", async () => {
    await act(async () => {
      root?.render(createElement(Button, null, "Save"));
      await flush();
    });
    const button = container?.querySelector("button");
    expect(button?.textContent).toBe("Save");
    expect(button?.getAttribute("type")).toBe("button");
    expect(button?.className).toContain("takt-btn-secondary");
  });

  it("applies the requested variant and size classes", async () => {
    await act(async () => {
      root?.render(createElement(Button, { variant: "danger", size: "sm" }, "Delete"));
      await flush();
    });
    const button = container?.querySelector("button");
    expect(button?.className).toContain("takt-btn-danger");
    expect(button?.className).toContain("takt-btn-sm");
  });

  it("disables the control and marks it busy while loading, without firing onClick", async () => {
    const onClick = vi.fn();
    await act(async () => {
      root?.render(createElement(Button, { loading: true, onClick }, "Save"));
      await flush();
    });
    const button = container?.querySelector("button") as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-busy")).toBe("true");
    button.click();
    expect(onClick).not.toHaveBeenCalled();
  });
});
