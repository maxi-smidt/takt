// @vitest-environment jsdom

import { act, createElement, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Checkbox } from "./Checkbox";

async function flush(times = 5) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
}

function ControlledCheckbox() {
  const [checked, setChecked] = useState(false);
  return createElement(Checkbox, { id: "override", checked, onCheckedChange: setChecked }, "Interrupt the run anyway");
}

describe("Checkbox", () => {
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

  it("toggles on click and reflects state via aria-checked", async () => {
    await act(async () => {
      root?.render(createElement(ControlledCheckbox));
      await flush();
    });
    const box = container?.querySelector("button[role='checkbox']") as HTMLButtonElement;
    expect(box.getAttribute("aria-checked")).toBe("false");

    await act(async () => {
      box.click();
      await flush();
    });
    expect(box.getAttribute("aria-checked")).toBe("true");
  });

  it("associates the visible label text via the wrapping <label for>", async () => {
    await act(async () => {
      root?.render(createElement(ControlledCheckbox));
      await flush();
    });
    const label = container?.querySelector("label");
    const box = container?.querySelector("button[role='checkbox']");
    expect(label?.getAttribute("for")).toBe(box?.id);
    expect(label?.textContent).toContain("Interrupt the run anyway");
  });
});
