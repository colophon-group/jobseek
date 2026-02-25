"use client";

import { useState, type InputHTMLAttributes } from "react";
import { useLingui } from "@lingui/react/macro";
import { Eye, EyeOff } from "lucide-react";

const inputClass =
  "w-full rounded-md border border-divider bg-background px-3 py-1.5 text-sm text-foreground outline-none focus:border-primary";

type FormFieldProps = {
  label: string;
  className?: string;
} & InputHTMLAttributes<HTMLInputElement>;

export function FormField({ label, className, type, ...inputProps }: FormFieldProps) {
  const { t } = useLingui();
  const isPassword = type === "password";
  const [showPassword, setShowPassword] = useState(false);

  return (
    <label className={`block ${className ?? ""}`}>
      <span className="mb-1 block text-sm font-medium">{label}</span>
      <div className="relative">
        <input
          className={`${inputClass}${isPassword ? " pr-9" : ""}`}
          type={isPassword && showPassword ? "text" : type}
          {...inputProps}
        />
        {isPassword && (
          <button
            type="button"
            onClick={() => setShowPassword((v) => !v)}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted hover:text-foreground transition-colors cursor-pointer"
            aria-label={showPassword
              ? t({ id: "form.password.hide", comment: "Aria label to hide password", message: "Hide password" })
              : t({ id: "form.password.show", comment: "Aria label to show password", message: "Show password" })
            }
            tabIndex={-1}
          >
            {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        )}
      </div>
    </label>
  );
}
