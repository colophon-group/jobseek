"use client";

import { useEffect, useRef, type HTMLAttributes } from "react";

type ErrorAlertProps = {
  message: string;
  focusOnRender?: boolean;
} & HTMLAttributes<HTMLDivElement>;

export function ErrorAlert({
  message,
  focusOnRender = false,
  className,
  tabIndex,
  ...props
}: ErrorAlertProps) {
  const localRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!message || !focusOnRender) return;
    localRef.current?.focus();
  }, [message, focusOnRender]);

  if (!message) return null;
  return (
    <div
      ref={localRef}
      role="alert"
      tabIndex={focusOnRender ? (tabIndex ?? -1) : tabIndex}
      className={`mb-4 rounded-md border border-error-border bg-error-bg px-4 py-3 text-sm text-error${className ? ` ${className}` : ""}`}
      {...props}
    >
      {message}
    </div>
  );
}
