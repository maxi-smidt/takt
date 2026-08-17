import type { Device } from "../shared/contracts";

export interface MaintenanceActionDefinition {
  label: string;
  group: "service" | "power" | "diagnose";
  capability: string;
  confirm: string;
  destructive?: boolean;
  overridable?: boolean;
  aftermath?: string;
}

export const MAINTENANCE_ACTIONS: Record<string, MaintenanceActionDefinition>;
export const ACTION_GROUPS: { id: string; label: string }[];
export function actionAvailability(action: string, device: Device): { enabled: boolean; reason: string };
export function requiresOverride(action: string, device: Device): boolean;
export function healthTone(healthChecks: { summary?: { fail: number; warn: number } } | undefined): "unknown" | "fail" | "warn" | "ok";
