"use client";

import type { ReactNode, ComponentProps } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useLingui } from "@lingui/react/macro";

export function SettingsDialog({
  title,
  description,
  children,
  onOpenAutoFocus,
  onCloseAutoFocus,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  onOpenAutoFocus?: ComponentProps<typeof Dialog.Content>["onOpenAutoFocus"];
  onCloseAutoFocus?: ComponentProps<typeof Dialog.Content>["onCloseAutoFocus"];
}) {
  const { t } = useLingui();
  return (
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40" />
      <Dialog.Content
        {...(!description ? { "aria-describedby": undefined } : {})}
        onOpenAutoFocus={onOpenAutoFocus}
        onCloseAutoFocus={onCloseAutoFocus}
        className="fixed left-1/2 top-1/2 z-50 max-h-[85dvh] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border border-divider bg-surface p-5 shadow-xl sm:p-6"
      >
        <div className="mb-4 flex items-center justify-between gap-4">
          <Dialog.Title className="text-base font-semibold">
            {title}
          </Dialog.Title>
          <Dialog.Close
            className="flex min-h-10 min-w-10 items-center justify-center rounded-md text-muted hover:bg-border-soft"
            aria-label={t({
              id: "common.actions.close",
              comment: "Close a settings dialog",
              message: "Close",
            })}
          >
            <X size={16} aria-hidden="true" />
          </Dialog.Close>
        </div>
        {description && (
          <Dialog.Description className="mb-5 text-sm leading-relaxed text-muted">
            {description}
          </Dialog.Description>
        )}
        {children}
      </Dialog.Content>
    </Dialog.Portal>
  );
}
