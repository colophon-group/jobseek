"use client";

import type { ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Trans } from "@lingui/react/macro";
import { SettingsDialog } from "../SettingsDialog";

/** Keep the current account value visible; reveal a form only for its own action. */
export function AccountEdit({
  title,
  value,
  description,
  children,
  action,
}: {
  title: string;
  value: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="border-b border-border-soft py-5">
      <Dialog.Root>
        <div className="flex items-center justify-between gap-5">
          <div className="min-w-0">
            <h2 className="text-sm font-medium">{title}</h2>
            <div className="mt-2 break-words text-xs text-muted">{value}</div>
          </div>
          <Dialog.Trigger className="min-h-11 shrink-0 rounded-full border border-divider px-4 text-xs font-semibold hover:bg-border-soft">
            {action ?? (
              <Trans
                id="settings.account.edit"
                comment="Open an account field edit dialog"
              >
                Edit
              </Trans>
            )}
          </Dialog.Trigger>
        </div>
        <SettingsDialog title={title} description={description}>
          {children}
        </SettingsDialog>
      </Dialog.Root>
    </section>
  );
}
