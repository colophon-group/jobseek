"use client";

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown } from "lucide-react";
import type { ReactNode } from "react";

export type SettingsOption = {
  value: string;
  label: string;
  description?: string;
  icon?: ReactNode;
  disabled?: boolean;
};

/** A compact, keyboard-operated chooser for short lists. */
export function SettingsChoice({
  label,
  value,
  options,
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  options: SettingsOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const current = options.find((option) => option.value === value);
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        disabled={disabled}
        aria-label={`${label}: ${current?.label ?? value}`}
        className="inline-flex min-h-11 max-w-full items-center gap-2 rounded-lg border border-divider bg-surface px-3 py-2 text-xs whitespace-nowrap outline-none hover:bg-border-soft focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50"
      >
        {current?.icon}
        <span>{current?.label ?? value}</span>
        <ChevronDown
          size={12}
          className="ml-1 shrink-0 text-muted"
          aria-hidden="true"
        />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={7}
          collisionPadding={16}
          className="z-50 max-h-[70dvh] max-w-[calc(100vw-2rem)] min-w-44 overflow-y-auto rounded-lg border border-divider bg-surface p-1 shadow-lg"
        >
          <DropdownMenu.RadioGroup
            value={value}
            onValueChange={onChange}
            aria-label={label}
          >
            {options.map((option) => (
              <DropdownMenu.RadioItem
                key={option.value}
                value={option.value}
                disabled={option.disabled}
                className="flex min-h-11 max-w-72 cursor-pointer items-start gap-3 rounded-md px-3 py-3 text-xs outline-none data-[highlighted]:bg-border-soft data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50"
              >
                {option.icon && (
                  <span className="mt-0.5 shrink-0">{option.icon}</span>
                )}
                <span className="min-w-0 flex-1">
                  <span className="block">{option.label}</span>
                  {option.description && (
                    <span className="mt-1 block text-[11px] leading-relaxed text-muted">
                      {option.description}
                    </span>
                  )}
                </span>
                <span className="mt-0.5 w-3.5 shrink-0">
                  <DropdownMenu.ItemIndicator>
                    <Check size={14} aria-hidden="true" />
                  </DropdownMenu.ItemIndicator>
                </span>
              </DropdownMenu.RadioItem>
            ))}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
