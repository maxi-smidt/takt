// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Dialog, DialogBody } from "./Dialog";

async function flush(times = 5) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
}

describe("Dialog", () => {
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
    document.querySelectorAll("[data-radix-portal]").forEach((node) => node.remove());
  });

  it("renders the eyebrow, title and body into a portal, labelled for assistive tech", async () => {
    await act(async () => {
      root?.render(
        createElement(
          Dialog,
          { title: "Restart Pi", eyebrow: "CONFIRM MAINTENANCE" },
          createElement(DialogBody, null, "You are about to restart this device."),
        ),
      );
      await flush();
    });
    const dialogEl = document.body.querySelector("[role='dialog']");
    expect(dialogEl).not.toBeNull();
    expect(dialogEl?.textContent).toContain("CONFIRM MAINTENANCE");
    expect(dialogEl?.textContent).toContain("Restart Pi");
    expect(dialogEl?.textContent).toContain("You are about to restart this device.");
  });

  it("calls onClose when the close button is activated", async () => {
    const onClose = vi.fn();
    await act(async () => {
      root?.render(createElement(Dialog, { title: "Restart Pi", onClose }));
      await flush();
    });
    const closeButton = document.body.querySelector("[aria-label='Close']") as HTMLButtonElement;
    await act(async () => {
      closeButton.click();
      await flush();
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
