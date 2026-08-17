import type { ReactNode } from "react";
import { Dialog } from "../../shared/ui";

interface ModalProps {
  title: string;
  eyebrow?: string;
  onClose: () => void;
  wide?: boolean;
  children?: ReactNode;
}

/**
 * Fleet portal's modal shell. A thin wrapper over the shared Dialog so the
 * six call sites (AccessModal, ConfirmModal, EnrollmentModal, ReleaseModal,
 * WifiModal, PasswordChange) don't need to change — but dialog semantics
 * (focus trap, Escape, scroll lock, ARIA) now come from Radix instead of
 * being hand-rolled.
 */
export function Modal({ title, eyebrow, onClose, wide = false, children }: ModalProps) {
  return (
    <Dialog title={title} eyebrow={eyebrow} wide={wide} onClose={onClose}>
      {children}
    </Dialog>
  );
}
