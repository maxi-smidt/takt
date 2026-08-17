// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Select } from "./Select";

async function flush(times = 5) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
}

const OPTIONS = [
  { value: "stable", label: "Stable" },
  { value: "beta", label: "Beta" },
];

describe("Select", () => {
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

  it("shows the selected option's label on a closed trigger", async () => {
    await act(async () => {
      root?.render(
        createElement(Select, { value: "beta", onValueChange: () => {}, options: OPTIONS, placeholder: "Choose…" }),
      );
      await flush();
    });
    const trigger = container?.querySelector("[role='combobox']");
    expect(trigger?.textContent).toBe("Beta");
    expect(trigger?.getAttribute("aria-expanded")).toBe("false");
  });

  it("shows the placeholder when nothing is selected", async () => {
    await act(async () => {
      root?.render(
        createElement(Select, { value: "", onValueChange: () => {}, options: OPTIONS, placeholder: "Choose…" }),
      );
      await flush();
    });
    expect(container?.querySelector("[role='combobox']")?.textContent).toBe("Choose…");
  });
});
