import type { ReactNode } from "react";

export type BadgeTone = "neutral" | "accent" | "success" | "warning" | "danger";

interface BadgeProps {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
}

export function Badge({ tone = "neutral", children, className }: BadgeProps) {
  const toneClass = tone === "neutral" ? null : `takt-badge-${tone}`;
  return <span className={["takt-badge", toneClass, className].filter(Boolean).join(" ")}>{children}</span>;
}
