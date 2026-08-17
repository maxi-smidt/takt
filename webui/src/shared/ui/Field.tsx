import { useId, type ReactNode } from "react";

export interface FieldRenderProps {
  id: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
}

interface FieldProps {
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  className?: string;
  children: (fieldProps: FieldRenderProps) => ReactNode;
}

/**
 * Label + hint/error wrapper. Generates a stable id and hands it (plus the
 * matching aria-describedby/aria-invalid) to `children` so any input-like
 * control (TextInput, Select, ...) can wire up its own accessibility props.
 */
export function Field({ label, hint, error, required, className, children }: FieldProps) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className={["takt-field", className].filter(Boolean).join(" ")}>
      <label className="takt-field-label" htmlFor={id}>
        {label}
        {required ? " *" : null}
      </label>
      {children({ id, "aria-describedby": describedBy, "aria-invalid": Boolean(error) })}
      {hint && !error ? (
        <span id={hintId} className="takt-field-hint">
          {hint}
        </span>
      ) : null}
      {error ? (
        <span id={errorId} className="takt-field-error" role="alert">
          {error}
        </span>
      ) : null}
    </div>
  );
}
