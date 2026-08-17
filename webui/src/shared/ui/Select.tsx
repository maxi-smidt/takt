import * as RadixSelect from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

interface SelectProps {
  value: string;
  onValueChange: (value: string) => void;
  options: SelectOption[];
  placeholder?: string;
  disabled?: boolean;
  id?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
  className?: string;
}

export function Select({
  value,
  onValueChange,
  options,
  placeholder,
  disabled,
  id,
  className,
  ...rest
}: SelectProps) {
  return (
    <RadixSelect.Root value={value} onValueChange={onValueChange} disabled={disabled}>
      <RadixSelect.Trigger
        id={id}
        className={["takt-select-trigger", className].filter(Boolean).join(" ")}
        {...rest}
      >
        <RadixSelect.Value placeholder={placeholder} />
        <RadixSelect.Icon className="takt-select-icon">
          <ChevronDown size={16} />
        </RadixSelect.Icon>
      </RadixSelect.Trigger>
      <RadixSelect.Portal>
        <RadixSelect.Content className="takt-select-content" position="popper" sideOffset={4}>
          <RadixSelect.Viewport className="takt-select-viewport">
            {options.map((option) => (
              <RadixSelect.Item
                key={option.value}
                value={option.value}
                disabled={option.disabled}
                className="takt-select-item"
              >
                <RadixSelect.ItemText>{option.label}</RadixSelect.ItemText>
                <RadixSelect.ItemIndicator>
                  <Check size={14} />
                </RadixSelect.ItemIndicator>
              </RadixSelect.Item>
            ))}
          </RadixSelect.Viewport>
        </RadixSelect.Content>
      </RadixSelect.Portal>
    </RadixSelect.Root>
  );
}
