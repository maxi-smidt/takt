import * as RadixDialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { IconButton } from "./IconButton";

interface DialogProps {
  title: string;
  eyebrow?: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Convenience for the common case: only cares that the dialog closed. */
  onClose?: () => void;
  wide?: boolean;
  children?: ReactNode;
}

export function Dialog({ title, eyebrow, open = true, onOpenChange, onClose, wide = false, children }: DialogProps) {
  const handleOpenChange = (next: boolean) => {
    onOpenChange?.(next);
    if (!next) onClose?.();
  };

  return (
    <RadixDialog.Root open={open} onOpenChange={handleOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="takt-dialog-overlay" />
        <RadixDialog.Content className={["takt-dialog", wide ? "takt-dialog-wide" : null].filter(Boolean).join(" ")}>
          <header className="takt-dialog-header">
            <div>
              {eyebrow ? <span className="takt-dialog-eyebrow">{eyebrow}</span> : null}
              <RadixDialog.Title className="takt-dialog-title">{title}</RadixDialog.Title>
            </div>
            <RadixDialog.Close asChild>
              <IconButton icon={<X size={18} />} aria-label="Close" variant="ghost" className="takt-dialog-close" />
            </RadixDialog.Close>
          </header>
          {children}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

export function DialogDescription({ children }: { children: ReactNode }) {
  return <RadixDialog.Description asChild>{children}</RadixDialog.Description>;
}

export function DialogBody({ children }: { children: ReactNode }) {
  return <div className="takt-dialog-body">{children}</div>;
}

export function DialogActions({ children }: { children: ReactNode }) {
  return <footer className="takt-dialog-actions">{children}</footer>;
}
