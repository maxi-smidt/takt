import type { CSSProperties, HTMLAttributes } from "react";

interface LayoutProps extends HTMLAttributes<HTMLDivElement> {
  gap?: CSSProperties["gap"];
}

/** Vertical flex layout helper. */
export function Stack({ gap = "10px", className, style, ...rest }: LayoutProps) {
  return (
    <div className={["takt-stack", className].filter(Boolean).join(" ")} style={{ gap, ...style }} {...rest} />
  );
}

/** Horizontal, wrapping flex layout helper. */
export function Cluster({ gap = "10px", className, style, ...rest }: LayoutProps) {
  return (
    <div className={["takt-cluster", className].filter(Boolean).join(" ")} style={{ gap, ...style }} {...rest} />
  );
}
