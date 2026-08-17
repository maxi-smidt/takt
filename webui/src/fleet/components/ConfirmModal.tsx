import { useState } from "react";
import type { Device } from "../../shared/contracts";
import { Button, Callout, Checkbox, DialogActions, DialogDescription } from "../../shared/ui";
import { MAINTENANCE_ACTIONS, requiresOverride } from "../maintenanceActions.js";
import { Modal } from "./Modal";

interface ConfirmModalProps {
  device: Device;
  action: string;
  onClose: () => void;
  onConfirm: (override: boolean) => void;
}

export function ConfirmModal({ device, action, onClose, onConfirm }: ConfirmModalProps) {
  // `action` always names a real entry — MAINTENANCE_ACTIONS mirrors the server-side
  // table the caller already validated against.
  const definition = MAINTENANCE_ACTIONS[action]!;
  const needsOverride = requiresOverride(action, device);
  const [override, setOverride] = useState(false);
  const effectiveOverride = needsOverride && override;
  const timerState = (device.status?.health as { state?: string } | undefined)?.state || "unknown";
  const blocked = needsOverride && !effectiveOverride;

  return (
    <Modal title={`${definition.label} · ${device.name}`} eyebrow="CONFIRM MAINTENANCE" onClose={onClose}>
      <div className="confirm-body">
        <DialogDescription>
          <p>
            You are about to {definition.confirm} on <strong>{device.name}</strong>.
          </p>
        </DialogDescription>
        {definition.aftermath && <p className="confirm-aftermath">{definition.aftermath}</p>}
        {needsOverride ? (
          <Callout tone="danger">
            <strong>THIS PI IS NOT IDLE</strong>
            <p>
              The timer is <strong>{timerState}</strong>. Continuing will interrupt a running or
              unsaved run and that measurement will be lost.
            </p>
            <Checkbox checked={effectiveOverride} onCheckedChange={setOverride}>
              Interrupt the run anyway
            </Checkbox>
          </Callout>
        ) : (
          <p className="confirm-safe">
            The Pi reports timer state <strong>{timerState}</strong>. The agent still re-checks this
            immediately before acting and waits if a run has started in the meantime.
          </p>
        )}
      </div>
      <DialogActions>
        <Button variant="secondary" onClick={onClose}>
          CANCEL
        </Button>
        <Button
          variant={definition.destructive ? "danger" : "primary"}
          disabled={blocked}
          onClick={() => onConfirm(effectiveOverride)}
        >
          {definition.label}
        </Button>
      </DialogActions>
    </Modal>
  );
}
