// @ts-nocheck
import { useState } from "react";
import { TriangleAlert } from "lucide-react";
import { MAINTENANCE_ACTIONS, requiresOverride } from "../maintenanceActions.js";
import { Modal } from "./Modal";

export function ConfirmModal({ device, action, onClose, onConfirm }) {
  const definition = MAINTENANCE_ACTIONS[action];
  const needsOverride = requiresOverride(action, device);
  const [override, setOverride] = useState(false);
  const effectiveOverride = needsOverride && override;
  const timerState = device.status?.health?.state || "unknown";
  const blocked = needsOverride && !effectiveOverride;
  return (
    <Modal title={`${definition.label} · ${device.name}`} eyebrow="CONFIRM MAINTENANCE" onClose={onClose}>
      <div className="confirm-body">
        <p>You are about to {definition.confirm} on <strong>{device.name}</strong>.</p>
        {definition.aftermath && <p className="confirm-aftermath">{definition.aftermath}</p>}
        {needsOverride ? (
          <div className="confirm-warning" role="alert">
            <TriangleAlert size={16} />
            <div>
              <strong>THIS PI IS NOT IDLE</strong>
              <span>
                The timer is <strong>{timerState}</strong>. Continuing will interrupt a running or
                unsaved run and that measurement will be lost.
              </span>
              <label className="confirm-override">
                <input
                  type="checkbox"
                  checked={effectiveOverride}
                  onChange={(event) => setOverride(event.target.checked)}
                />
                Interrupt the run anyway
              </label>
            </div>
          </div>
        ) : (
          <p className="confirm-safe">
            The Pi reports timer state <strong>{timerState}</strong>. The agent still re-checks this
            immediately before acting and waits if a run has started in the meantime.
          </p>
        )}
      </div>
      <footer className="modal-actions">
        <button className="secondary-button" onClick={onClose}>CANCEL</button>
        <button
          className={definition.destructive ? "danger-action" : ""}
          disabled={blocked}
          onClick={() => onConfirm(effectiveOverride)}
        >
          {definition.label}
        </button>
      </footer>
    </Modal>
  );
}
