"use client";

import { forwardRef, useId, useState, type InputHTMLAttributes } from "react";
import { useLingui } from "@lingui/react/macro";
import { Eye, EyeOff } from "lucide-react";

const inputClass =
  "w-full rounded-md border border-divider bg-background px-3 py-1.5 text-sm text-foreground outline-none focus:border-primary";

type FormFieldProps = {
  label: string;
  className?: string;
  error?: string | null;
  hint?: string | null;
  hintClassName?: string;
} & InputHTMLAttributes<HTMLInputElement>;

export const FormField = forwardRef<HTMLInputElement, FormFieldProps>(function FormField({
  label,
  className,
  type,
  id,
  error,
  hint,
  hintClassName,
  "aria-describedby": ariaDescribedBy,
  "aria-invalid": ariaInvalid,
  ...inputProps
}, ref) {
  const { t } = useLingui();
  const generatedId = useId();
  const isPassword = type === "password";
  const [showPassword, setShowPassword] = useState(false);
  const inputId = id ?? generatedId;
  const hintId = hint ? `${inputId}-hint` : undefined;
  const errorId = error ? `${inputId}-error` : undefined;
  const describedBy = [ariaDescribedBy, hintId, errorId].filter(Boolean).join(" ") || undefined;
  const invalid = error ? true : ariaInvalid;

  return (
    <div className={`block ${className ?? ""}`}>
      <label htmlFor={inputId} className="mb-1 block text-sm font-medium">{label}</label>
      <div className="relative">
        <input
          id={inputId}
          ref={ref}
          className={`${inputClass}${isPassword ? " pr-9" : ""}`}
          type={isPassword && showPassword ? "text" : type}
          aria-describedby={describedBy}
          aria-invalid={invalid}
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
            {showPassword ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
          </button>
        )}
      </div>
      {hint && (
        <p id={hintId} className={hintClassName ?? "mt-1 text-xs text-muted"}>
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="mt-1 text-xs text-error">
          {error}
        </p>
      )}
    </div>
  );
});
