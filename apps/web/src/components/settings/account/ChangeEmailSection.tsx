"use client";

import { AccountEdit } from "./AccountEdit";
import { useSession } from "@/components/providers/SessionProvider";
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
  const { user } = useSession();
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
      const message = t({
        id: "settings.account.email.required",
        comment: "Error when email field is empty",
        message: "Please enter a new email address",
      });
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
      setError(
        error.message ??
          t({
            id: "settings.account.email.error",
            comment: "Generic email change error",
            message: "Failed to change email",
          }),
      );
      return;
    }

    setSuccess(
      t({
        id: "settings.account.email.success",
        comment: "Success message after email change request",
        message:
          "Verification email sent. Please check your inbox. It may take a few minutes to arrive.",
      }),
    );
    setNewEmail("");
  }

  return (
    <AccountEdit
      title={t({
        id: "settings.account.email.address",
        comment: "Current account email row heading",
        message: "Email address",
      })}
      value={
        <>
          <span className="block">{user?.email}</span>
          <span
            className={`mt-2 inline-flex items-center gap-[9px] text-[11px] ${user?.emailVerified ? "text-green-600 dark:text-green-400" : "text-muted"}`}
          >
            <span
              className="h-1 w-1 rounded-full bg-current"
              aria-hidden="true"
            />
            {user?.emailVerified
              ? t({
                  id: "settings.account.email.verified",
                  comment: "Current account email has been verified",
                  message: "Verified",
                })
              : t({
                  id: "settings.account.email.unverified",
                  comment: "Current account email is awaiting verification",
                  message: "Not verified",
                })}
          </span>
        </>
      }
      description={t({
        id: "settings.account.email.description",
        comment: "Change email section description",
        message: "Update the email address associated with your account.",
      })}
      action={
        <Trans
          id="settings.account.email.change"
          comment="Open email change dialog"
        >
          Change
        </Trans>
      }
    >
      <ErrorAlert message={error} focusOnRender />
      <SuccessAlert message={success} />
      <form
        onSubmit={handleSubmit}
        className="flex flex-col gap-4 min-[480px]:flex-row min-[480px]:items-end"
      >
        <div className="flex-1">
          <FormField
            label={t({
              id: "settings.account.email.label",
              comment: "New email input label",
              message: "New email",
            })}
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
            ? t({
                id: "settings.account.email.saving",
                comment: "Email save button while loading",
                message: "Saving...",
              })
            : t({
                id: "settings.account.email.save",
                comment: "Email save button",
                message: "Update email",
              })}
        </Button>
      </form>
    </AccountEdit>
  );
}
