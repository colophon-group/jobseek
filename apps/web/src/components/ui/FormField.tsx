import type { InputHTMLAttributes } from "react";

const inputClass =
  "w-full rounded-md border border-border-soft bg-background px-3 py-2 text-foreground outline-none focus:border-primary";

type FormFieldProps = {
  label: string;
  className?: string;
} & InputHTMLAttributes<HTMLInputElement>;

export function FormField({ label, className, ...inputProps }: FormFieldProps) {
  return (
    <label className={`block ${className ?? ""}`}>
      <span className="mb-1 block text-sm font-medium">{label}</span>
      <input className={inputClass} {...inputProps} />
    </label>
  );
}
