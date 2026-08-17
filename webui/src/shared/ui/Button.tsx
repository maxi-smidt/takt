import { Slot } from "@radix-ui/react-slot";
import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Render the styling onto a single child element (e.g. an anchor) instead of a <button>. */
  asChild?: boolean;
  loading?: boolean;
  children?: ReactNode;
}

export function Button({
  variant = "secondary",
  size = "md",
  asChild = false,
  loading = false,
  className,
  type = "button",
  disabled,
  children,
  ...rest
}: ButtonProps) {
  const classes = [
    "takt-btn",
    `takt-btn-${variant}`,
    size === "sm" ? "takt-btn-sm" : null,
    className,
  ]
    .filter(Boolean)
    .join(" ");

  if (asChild) {
    return (
      <Slot className={classes} aria-busy={loading || undefined} {...rest}>
        {children}
      </Slot>
    );
  }

  return (
    <button
      type={type}
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {children}
    </button>
  );
}
