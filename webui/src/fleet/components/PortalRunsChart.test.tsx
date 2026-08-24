// @vitest-environment jsdom

import { act, cloneElement, createElement, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// jsdom has no ResizeObserver, so recharts' ResponsiveContainer never measures
// a size and never mounts its children. Replace it with a passthrough that
// renders the chart at a fixed size — the real ComposedChart/Line/Area
// underneath are untouched, so this still exercises the actual rendering path.
vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: (
      { children, height }: { children: ReactElement<{ width?: number; height?: number }>; height?: number },
    ) => cloneElement(children, { width: 760, height: typeof height === "number" ? height : 260 }),
  };
});

const { PortalRunsChart } = await import("./PortalRunsChart");
type PortalRun = import("./PortalRunsChart").PortalRun;

function run(overrides: Partial<PortalRun> & Pick<PortalRun, "id" | "actual_time_ms" | "added_time_ms">): PortalRun {
  return {
    run_number: 1,
    session_date: "2026-08-10",
    started_at: "2026-08-10T10:00:00Z",
    total_time_ms: overrides.actual_time_ms + overrides.added_time_ms,
    ...overrides,
  };
}

describe("PortalRunsChart", () => {
  let root: Root | null = null;
  let container: HTMLDivElement | null = null;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(async () => {
    if (root) {
      await act(async () => {
        root?.unmount();
      });
      root = null;
    }
    container?.remove();
    container = null;
  });

  it("draws Ist-Zeit as an open line and keeps the Fehler band pinned to it, instead of a closed shape stroked down to the axis floor", async () => {
    const runs = [
      run({ id: 1, run_number: 1, session_date: "2026-08-10", started_at: "2026-08-10T10:00:00Z", actual_time_ms: 40000, added_time_ms: 5000 }),
      run({ id: 2, run_number: 2, session_date: "2026-08-11", started_at: "2026-08-11T10:00:00Z", actual_time_ms: 42000, added_time_ms: 0 }),
      run({ id: 3, run_number: 3, session_date: "2026-08-12", started_at: "2026-08-12T10:00:00Z", actual_time_ms: 44000, added_time_ms: 3000 }),
    ];

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root?.render(createElement(PortalRunsChart, { runs, bestTotalMs: 42000 }));
    });

    const linePath = container.querySelector("path.recharts-line-curve");
    const areaPath = container.querySelector("path.recharts-area-area");
    expect(linePath).toBeTruthy();
    expect(areaPath).toBeTruthy();

    const lineD = linePath!.getAttribute("d")!;
    const areaD = areaPath!.getAttribute("d")!;

    // A plain Line never closes its path. An Area (even with fill="none",
    // which the old implementation used for Ist-Zeit) always strokes a
    // closed shape back down to the y-axis floor — that stray outline was
    // the "wasted time shown down to zero" artifact.
    expect(lineD.trim()).not.toMatch(/Z$/);

    // The Fehler band's baseline (its "L" jump back to the start of the
    // return path) must land on the Ist-Zeit line's own last point, proving
    // the band is pinned to Ist-Zeit rather than to the axis floor.
    const [, lastLineX, lastLineY] = /(-?[\d.]+),(-?[\d.]+)\s*$/.exec(lineD)!;
    const [, areaJumpX, areaJumpY] = /L(-?[\d.]+),(-?[\d.]+)/.exec(areaD)!;
    expect(Number(areaJumpX)).toBeCloseTo(Number(lastLineX), 3);
    expect(Number(areaJumpY)).toBeCloseTo(Number(lastLineY), 3);
  });
});
