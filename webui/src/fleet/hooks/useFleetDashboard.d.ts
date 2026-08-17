import type { BundledReleaseStatus, Device, Job, Release } from "../../shared/contracts";
import type { DiagnosticsBundle } from "../components/MaintenancePanel";

interface UseFleetDashboardArgs {
  session: { csrf_token: string };
  refreshSession: () => Promise<void>;
}

export function useFleetDashboard(args: UseFleetDashboardArgs): {
  devices: Device[];
  releases: Release[];
  bundledRelease: BundledReleaseStatus | null;
  jobs: Job[];
  diagnostics: Record<string, DiagnosticsBundle[]>;
  error: string;
  online: number;
  mirroredRuns: number;
  insecureLan: boolean;
  load: () => Promise<void>;
  submitJob: (device: Device, action: string, payload?: Record<string, unknown>, override?: boolean) => Promise<void>;
  createJob: (device: Device, action: string, payload?: Record<string, unknown>) => Promise<void>;
  cancelJob: (job: Job) => Promise<void>;
  retryJob: (job: Job) => Promise<void>;
  forceClearJob: (job: Job) => Promise<void>;
  acknowledgeRecovery: (device: Device) => Promise<void>;
  revokeDevice: (device: Device) => Promise<void>;
  uninstallRelease: (release: Release) => Promise<void>;
  logout: () => Promise<void>;
};
