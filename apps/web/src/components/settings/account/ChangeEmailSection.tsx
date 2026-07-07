"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { Trans, useLingui } from "@lingui/react/macro";
import { Button } from "@/components/ui/Button";
import { ErrorAlert } from "@/components/ui/ErrorAlert";
import { FormField } from "@/components/ui/FormField";
import { SuccessAlert } from "@/components/ui/SuccessAlert";
import { authClient } from "@/lib/auth-client";
import { useLocalePath } from "@/lib/useLocalePath";

export function ChangeEmailSection() {
  const { t } = useLingui();
  const lp = useLocalePath();
  const [newEmail, setNewEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [emailError, setEmailError] = useState("");
  const [success, setSuccess] = useState("");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setEmailError("");
    setSuccess("");

    if (!newEmail) {
      const message = t({ id: "settings.account.email.required", comment: "Error when email field is empty", message: "Please enter a new email address" });
      setEmailError(message);
      setError(message);
      return;
    }

    setLoading(true);
    const { error } = await authClient.changeEmail({
      newEmail,
      callbackURL: lp("/settings/account"),
    });
    setLoading(false);

    if (error) {
      setEmailError("");
      setError(error.message ?? t({ id: "settings.account.email.error", comment: "Generic email change error", message: "Failed to change email" }));
      return;
    }

    setSuccess(t({ id: "settings.account.email.success", comment: "Success message after email change request", message: "Verification email sent. Please check your inbox. It may take a few minutes to arrive." }));
    setNewEmail("");
  }

  return (
    <section>
      <h2 className="mb-1 text-base font-semibold">
        <Trans id="settings.account.email.title" comment="Change email section heading">Change email</Trans>
      </h2>
      <p className="mb-4 text-sm text-muted">
        <Trans id="settings.account.email.description" comment="Change email section description">
          Update the email address associated with your account.
        </Trans>
      </p>
      <ErrorAlert message={error} focusOnRender />
      <SuccessAlert message={success} />
      <form onSubmit={handleSubmit} className="flex flex-col gap-4 min-[480px]:flex-row min-[480px]:items-end">
        <div className="flex-1">
          <FormField
            label={t({ id: "settings.account.email.label", comment: "New email input label", message: "New email" })}
            type="email"
            required
            autoComplete="email"
            value={newEmail}
            onChange={(e) => {
              setNewEmail(e.target.value);
              setEmailError("");
            }}
            error={emailError}
          />
        </div>
        <Button type="submit" disabled={loading} size="sm">
          {loading
            ? t({ id: "settings.account.email.saving", comment: "Email save button while loading", message: "Saving..." })
            : t({ id: "settings.account.email.save", comment: "Email save button", message: "Update email" })}
        </Button>
      </form>
    </section>
  );
}
