import type { InputHTMLAttributes } from "react";

export function TextInput({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={["takt-input", className].filter(Boolean).join(" ")} {...rest} />;
}
