import type { SessionResponse } from "../../shared/contracts";
import type { PortalRun } from "../components/PortalRunsChart";

export type { PortalRun };

export interface PortalDevice {
  id: string;
  name: string;
  mirror_state: string;
  run_count?: number;
  last_mirrored_at?: string | null;
  access?: string;
}

export interface PortalRunsPayload {
  summary: { count: number; best_total_ms: number; average_actual_ms: number; average_total_ms: number };
  mirror: { state: string; last_mirrored_at?: string | null; sha256?: string };
  runs: PortalRun[];
}

export interface PendingRunAction {
  run: PortalRun;
  operation: "adjust_added_time" | "delete";
  desired?: number;
}

interface UsePortalRunsArgs {
  session: SessionResponse;
  refreshSession: () => Promise<void>;
}

export function usePortalRuns(args: UsePortalRunsArgs): {
  devices: PortalDevice[];
  deviceId: string;
  setDeviceId: (id: string) => void;
  runs: PortalRunsPayload | null;
  from: string;
  setFrom: (value: string) => void;
  to: string;
  setTo: (value: string) => void;
  error: string;
  loadDevices: () => Promise<void>;
  logout: () => Promise<void>;
  pendingAction: PendingRunAction | null;
  requestCommand: (run: PortalRun, operation: "adjust_added_time" | "delete", desired?: number) => void;
  cancelPendingAction: () => void;
  confirmPendingAction: () => Promise<void>;
};
