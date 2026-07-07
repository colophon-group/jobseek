"use client";

import { useState } from "react";
import { Trans, useLingui } from "@lingui/react/macro";
import { Button } from "@/components/ui/Button";
import { ErrorAlert } from "@/components/ui/ErrorAlert";
import { authClient } from "@/lib/auth-client";

export function DeleteAccountSection() {
  const { t } = useLingui();
  const [showConfirm, setShowConfirm] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

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
      <ErrorAlert message={error} focusOnRender />
      {!showConfirm ? (
        <Button onClick={() => setShowConfirm(true)} variant="danger" size="sm">
          {t({ id: "settings.account.delete.button", comment: "Delete account button", message: "Delete my account" })}
        </Button>
      ) : (
        <div className="flex gap-2">
          <Button onClick={handleDelete} disabled={loading} variant="danger" size="sm">
            {loading
              ? t({ id: "settings.account.delete.deleting", comment: "Delete button while loading", message: "Deleting..." })
              : t({ id: "settings.account.delete.confirm", comment: "Confirm delete button", message: "Confirm deletion" })}
          </Button>
          <Button
            onClick={() => {
              setShowConfirm(false);
              setError("");
            }}
            variant="danger-outline"
            size="sm"
          >
            {t({ id: "settings.account.delete.cancel", comment: "Cancel delete button", message: "Cancel" })}
          </Button>
        </div>
      )}
    </section>
  );
}
