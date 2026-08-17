// @ts-nocheck
import { Download } from "lucide-react";
import { ACTION_GROUPS, MAINTENANCE_ACTIONS, actionAvailability } from "../maintenanceActions.js";
import { bytes, timeAgo } from "../formatters";

export function MaintenancePanel({ device, diagnostics, onAction }) {
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
                  <button
                    key={action}
                    className={definition.destructive ? "danger-action" : ""}
                    disabled={!enabled}
                    title={reason}
                    onClick={() => onAction(device, action)}
                  >
                    {definition.label}
                  </button>
                );
              })}
          </div>
        </div>
      ))}
      {diagnostics?.length > 0 && (
        <div className="maintenance-group">
          <span className="maintenance-label">BUNDLES</span>
          <div className="maintenance-bundles">
            {diagnostics.map((bundle) => (
              <a
                key={bundle.id}
                href={`/api/devices/${device.id}/diagnostics/${bundle.id}`}
                title={`${bytes(bundle.size)} · redacted diagnostics`}
              >
                <Download size={13} /> {timeAgo(bundle.created_at)}
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
