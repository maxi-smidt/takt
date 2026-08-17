import { Download } from "lucide-react";
import type { Device } from "../../shared/contracts";
import { Button } from "../../shared/ui";
import { bytes, timeAgo } from "../formatters";
import { ACTION_GROUPS, MAINTENANCE_ACTIONS, actionAvailability } from "../maintenanceActions.js";

export interface DiagnosticsBundle {
  id: string;
  size?: number;
  created_at?: string;
}

interface MaintenancePanelProps {
  device: Device;
  diagnostics?: DiagnosticsBundle[];
  onAction: (device: Device, action: string) => void;
}

export function MaintenancePanel({ device, diagnostics, onAction }: MaintenancePanelProps) {
  return (
    <div className="maintenance-panel">
      {ACTION_GROUPS.map((group) => (
        <div className="maintenance-group" key={group.id}>
          <span className="maintenance-label">{group.label}</span>
          <div className="maintenance-buttons">
            {Object.entries(MAINTENANCE_ACTIONS)
              .filter(([, definition]) => definition.group === group.id)
              .map(([action, definition]) => {
                const { enabled, reason } = actionAvailability(action, device);
                return (
                  <Button
                    key={action}
                    variant={definition.destructive ? "danger" : "secondary"}
                    size="sm"
                    className="maintenance-button"
                    disabled={!enabled}
                    title={reason}
                    onClick={() => onAction(device, action)}
                  >
                    {definition.label}
                  </Button>
                );
              })}
          </div>
        </div>
      ))}
      {diagnostics && diagnostics.length > 0 && (
        <div className="maintenance-group">
          <span className="maintenance-label">BUNDLES</span>
          <div className="maintenance-bundles">
            {diagnostics.map((bundle) => (
              <Button
                asChild
                variant="secondary"
                size="sm"
                key={bundle.id}
              >
                <a
                  href={`/api/devices/${device.id}/diagnostics/${bundle.id}`}
                  title={`${bytes(bundle.size)} · redacted diagnostics`}
                >
                  <Download size={13} /> {timeAgo(bundle.created_at)}
                </a>
              </Button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
