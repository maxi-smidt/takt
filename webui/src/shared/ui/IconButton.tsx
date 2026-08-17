import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Button, type ButtonVariant } from "./Button";

interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  variant?: ButtonVariant;
  icon: ReactNode;
  "aria-label": string;
}

export function IconButton({ variant = "ghost", icon, className, ...rest }: IconButtonProps) {
  const classes = ["takt-icon-btn", className].filter(Boolean).join(" ");
  return (
    <Button variant={variant} className={classes} {...rest}>
      {icon}
    </Button>
  );
}
