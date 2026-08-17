import { Info, OctagonAlert, TriangleAlert } from "lucide-react";
import type { ComponentType, ReactNode } from "react";

export type CalloutTone = "info" | "warning" | "danger";

const ICONS: Record<CalloutTone, ComponentType<{ size?: number; className?: string }>> = {
  info: Info,
  warning: TriangleAlert,
  danger: OctagonAlert,
};

interface CalloutProps {
  tone?: CalloutTone;
  children: ReactNode;
  className?: string;
}

export function Callout({ tone = "info", children, className }: CalloutProps) {
  const Icon = ICONS[tone];
  return (
    <div
      className={["takt-callout", `takt-callout-${tone}`, className].filter(Boolean).join(" ")}
      role={tone === "danger" ? "alert" : undefined}
    >
      <Icon size={16} className="takt-callout-icon" />
      <div>{children}</div>
    </div>
  );
}
