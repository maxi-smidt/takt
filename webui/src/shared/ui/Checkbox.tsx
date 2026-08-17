import * as RadixCheckbox from "@radix-ui/react-checkbox";
import { Check } from "lucide-react";
import type { ReactNode } from "react";

interface CheckboxProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  children?: ReactNode;
  disabled?: boolean;
  id?: string;
  className?: string;
}

export function Checkbox({ checked, onCheckedChange, children, disabled, id, className }: CheckboxProps) {
  return (
    <label className={["takt-checkbox-row", className].filter(Boolean).join(" ")} htmlFor={id}>
      <RadixCheckbox.Root
        id={id}
        className="takt-checkbox"
        checked={checked}
        onCheckedChange={(state) => onCheckedChange(state === true)}
        disabled={disabled}
      >
        <RadixCheckbox.Indicator>
          <Check size={14} />
        </RadixCheckbox.Indicator>
      </RadixCheckbox.Root>
      {children}
    </label>
  );
}
