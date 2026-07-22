"use client";

import { useRef, useState } from "react";
import { Trans, useLingui } from "@lingui/react/macro";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { Button } from "@/components/ui/Button";
import { ErrorAlert } from "@/components/ui/ErrorAlert";
import { authClient } from "@/lib/auth-client";

export function DeleteAccountSection() {
  const { t } = useLingui();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const deleteButtonRef = useRef<HTMLButtonElement>(null);

  function handleOpenChange(open: boolean) {
    setConfirmOpen(open);
    if (!open) setError("");
  }

  async function handleDelete() {
    setError("");
    setLoading(true);
    const { error } = await authClient.deleteUser({});
    setLoading(false);

    if (error) {
      setError(error.message ?? t({ id: "settings.account.delete.error", comment: "Generic account deletion error", message: "Failed to delete account" }));
      return;
    }

    window.location.href = "/";
  }

  return (
    <section className="rounded-md border border-error-border bg-error-bg p-4">
      <h2 className="mb-1 text-base font-semibold text-error">
        <Trans id="settings.account.delete.title" comment="Delete account section heading">Delete account</Trans>
      </h2>
      <p className="mb-4 text-sm text-error">
        <Trans id="settings.account.delete.description" comment="Delete account section description">
          Permanently delete your account and all associated data. This action cannot be undone.
        </Trans>
      </p>
      <AlertDialog.Root open={confirmOpen} onOpenChange={handleOpenChange}>
        <Button
          ref={deleteButtonRef}
          onClick={() => setConfirmOpen(true)}
          variant="danger"
          size="sm"
        >
          {t({ id: "settings.account.delete.button", comment: "Delete account button", message: "Delete my account" })}
        </Button>
        <AlertDialog.Portal>
          <AlertDialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
          <AlertDialog.Content
            className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border-soft bg-surface p-6 shadow-xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              deleteButtonRef.current?.focus();
            }}
          >
            <AlertDialog.Title className="text-base font-semibold">
              <Trans id="settings.account.delete.title" comment="Delete account section heading">Delete account</Trans>
            </AlertDialog.Title>
            <AlertDialog.Description className="mt-2 text-sm text-muted">
              <Trans id="settings.account.delete.description" comment="Delete account section description">
                Permanently delete your account and all associated data. This action cannot be undone.
              </Trans>
            </AlertDialog.Description>
            <div className="mt-4">
              <ErrorAlert message={error} focusOnRender />
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <AlertDialog.Cancel asChild>
                <Button variant="danger-outline" size="sm" disabled={loading}>
                  {t({ id: "settings.account.delete.cancel", comment: "Cancel delete button", message: "Cancel" })}
                </Button>
              </AlertDialog.Cancel>
              <Button onClick={handleDelete} disabled={loading} variant="danger" size="sm">
                {loading
                  ? t({ id: "settings.account.delete.deleting", comment: "Delete button while loading", message: "Deleting..." })
                  : t({ id: "settings.account.delete.confirm", comment: "Confirm delete button", message: "Confirm deletion" })}
              </Button>
            </div>
          </AlertDialog.Content>
        </AlertDialog.Portal>
      </AlertDialog.Root>
    </section>
  );
}
