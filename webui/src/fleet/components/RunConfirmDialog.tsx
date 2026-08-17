import { Button, DialogActions, DialogDescription } from "../../shared/ui";
import { formatDate, formatStopwatch } from "../formatters";
import type { PendingRunAction } from "../hooks/usePortalRuns";
import { Modal } from "./Modal";

interface RunConfirmDialogProps {
  action: PendingRunAction;
  onCancel: () => void;
  onConfirm: () => void;
}

export function RunConfirmDialog({ action, onCancel, onConfirm }: RunConfirmDialogProps) {
  const { run, operation, desired } = action;
  const isDelete = operation === "delete";

  return (
    <Modal
      title={isDelete ? "LAUF LÖSCHEN" : "FEHLERZEIT ÄNDERN"}
      eyebrow={isDelete ? "UNWIDERRUFLICH" : "KORREKTUR BESTÄTIGEN"}
      onClose={onCancel}
    >
      <div className="confirm-body">
        <DialogDescription>
          {isDelete ? (
            <p>
              Lauf <strong>#{run.run_number}</strong> vom {formatDate(run.session_date)} wird
              endgültig gelöscht und kann nicht wiederhergestellt werden.
            </p>
          ) : (
            <p>
              Fehlerzeit für Lauf <strong>#{run.run_number}</strong> vom {formatDate(run.session_date)}{" "}
              wird von {formatStopwatch(run.added_time_ms)} auf{" "}
              <strong>{formatStopwatch(desired ?? 0)}</strong> geändert.
            </p>
          )}
        </DialogDescription>
      </div>
      <DialogActions>
        <Button variant="secondary" onClick={onCancel}>ABBRECHEN</Button>
        <Button variant={isDelete ? "danger" : "primary"} onClick={onConfirm}>
          {isDelete ? "ENDGÜLTIG LÖSCHEN" : "ÜBERNEHMEN"}
        </Button>
      </DialogActions>
    </Modal>
  );
}
