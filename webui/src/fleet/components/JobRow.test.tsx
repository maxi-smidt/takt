// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Job } from "../../shared/contracts";
import { JobRow } from "./JobRow";

async function flush(times = 5) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
}

function baseJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "job-1",
    device_id: "d1",
    device_name: "Bahn 1",
    action: "mirror_now",
    status: "queued",
    updated_at: "2026-08-17T12:00:00Z",
    ...overrides,
  };
}

describe("JobRow", () => {
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

  async function renderJob(job: Job) {
    await act(async () => {
      root?.render(
        createElement(JobRow, {
          job,
          onCancel: vi.fn(),
          onRetry: vi.fn(),
          onForceClear: vi.fn(),
          onDelete: vi.fn(),
        }),
      );
      await flush();
    });
  }

  it("maps a known action to its readable label", async () => {
    await renderJob(baseJob({ action: "mirror_now" }));
    expect(container?.textContent).toContain("Mirror runs");
  });

  it("falls back to a humanized action name for an unknown action", async () => {
    await renderJob(baseJob({ action: "some_future_action" }));
    expect(container?.textContent).toContain("some future action");
  });

  it("explains a job waiting because the timer is busy", async () => {
    await renderJob(baseJob({ status: "queued", stage: "waiting_for_safe_state" }));
    expect(container?.textContent).toContain("Waiting for a safe state");
  });

  it("explains a job waiting because the device is offline", async () => {
    await renderJob(
      baseJob({
        status: "queued",
        device_online: false,
        device_last_seen_at: "2026-08-17T11:00:00Z",
      }),
    );
    expect(container?.textContent).toContain("Device is offline");
  });

  it("shows the plain waiting message for an ordinary queued job", async () => {
    await renderJob(baseJob({ status: "queued", device_online: true }));
    expect(container?.textContent).toContain("Waiting for the device to claim this job");
  });

  function labelsOf(root: HTMLDivElement | null): string[] {
    return Array.from(root?.querySelectorAll("button[aria-label]") || []).map(
      (button) => button.getAttribute("aria-label") || "",
    );
  }

  it("offers cancel and force-clear for an active install, but not retry or remove", async () => {
    await renderJob(baseJob({ action: "install_release", status: "running", stage: "downloading" }));
    expect(labelsOf(container)).toEqual(expect.arrayContaining(["Cancel", "Force clear"]));
    expect(labelsOf(container)).not.toContain("Retry");
    expect(labelsOf(container)).not.toContain("Remove");
  });

  it("hides cancel once an install has passed its checkpoint, but keeps force-clear", async () => {
    await renderJob(baseJob({ action: "install_release", status: "running", stage: "activating" }));
    expect(labelsOf(container)).not.toContain("Cancel");
    expect(labelsOf(container)).toContain("Force clear");
  });

  it("offers retry and remove for a failed job, but not force-clear", async () => {
    await renderJob(baseJob({ status: "failed" }));
    expect(labelsOf(container)).toEqual(expect.arrayContaining(["Retry", "Remove"]));
    expect(labelsOf(container)).not.toContain("Force clear");
  });

  it("never offers retry for a failed Wi-Fi job, since its credential isn't retained", async () => {
    await renderJob(baseJob({ action: "add_wifi_network", status: "failed" }));
    expect(labelsOf(container)).not.toContain("Retry");
    expect(labelsOf(container)).toContain("Remove");
  });

  it("offers only remove for a succeeded job", async () => {
    await renderJob(baseJob({ status: "succeeded" }));
    expect(labelsOf(container)).toEqual(["Remove"]);
  });
});
