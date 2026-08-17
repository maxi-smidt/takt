// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Field, type FieldRenderProps } from "./Field";
import { TextInput } from "./TextInput";

function renderInput(fieldProps: FieldRenderProps) {
  return createElement(TextInput, { ...fieldProps, value: "", onChange: () => {} });
}

async function flush(times = 5) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
}

describe("Field", () => {
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

  it("wires the label, hint and generated id to the rendered control", async () => {
    await act(async () => {
      root?.render(
        createElement(Field, { label: "Name", hint: "As shown in the roster", children: renderInput }),
      );
      await flush();
    });
    const label = container?.querySelector("label");
    const input = container?.querySelector("input");
    expect(label?.textContent).toBe("Name");
    expect(label?.getAttribute("for")).toBe(input?.id);
    expect(input?.getAttribute("aria-describedby")).toContain("hint");
    expect(input?.getAttribute("aria-invalid")).toBe("false");
    expect(container?.textContent).toContain("As shown in the roster");
  });

  it("marks the control invalid and shows the error instead of the hint", async () => {
    await act(async () => {
      root?.render(
        createElement(Field, {
          label: "Name",
          hint: "Ignored while invalid",
          error: "Required",
          children: renderInput,
        }),
      );
      await flush();
    });
    const input = container?.querySelector("input");
    expect(input?.getAttribute("aria-invalid")).toBe("true");
    expect(container?.textContent).not.toContain("Ignored while invalid");
    expect(container?.querySelector("[role='alert']")?.textContent).toBe("Required");
  });
});
