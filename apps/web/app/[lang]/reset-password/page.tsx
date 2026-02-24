"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { CircleCheck } from "lucide-react";
import { authClient } from "@/lib/auth-client";
import { useLocalePath } from "@/lib/useLocalePath";
import { AuthShell } from "@/components/AuthShell";
import { Button } from "@/components/ui/Button";
import { FormField } from "@/components/ui/FormField";
import { ErrorAlert } from "@/components/ui/ErrorAlert";

export default function ResetPasswordPage() {
  const searchParams = useSearchParams();
  const { t } = useLingui();
  const lp = useLocalePath();
  const token = searchParams.get("token");

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);

  if (!token) {
    return (
      <AuthShell>
        <div className="text-center">
          <h2 className="text-xl font-bold">
            <Trans id="auth.resetPassword.error.title" comment="Heading when reset link is invalid">
              Invalid reset link
            </Trans>
          </h2>
          <p className="mt-2 text-sm text-muted">
            <Trans id="auth.resetPassword.error.noToken" comment="Message when reset token is missing">
              This password reset link is invalid or has expired. Please request a new one.
            </Trans>
          </p>
          <Button href={lp("/sign-in")} prefetch={false} className="mt-4">
            <Trans id="auth.resetPassword.backToSignIn" comment="Link back to sign-in from reset password">
              Back to sign in
            </Trans>
          </Button>
        </div>
      </AuthShell>
    );
  }

  if (success) {
    return (
      <AuthShell>
        <div className="text-center">
          <CircleCheck className="mx-auto mb-4 size-10 text-green-500" />
          <h2 className="text-xl font-bold">
            <Trans id="auth.resetPassword.success.title" comment="Heading after password is successfully reset">
              Password updated
            </Trans>
          </h2>
          <p className="mt-2 text-sm text-muted">
            <Trans id="auth.resetPassword.success.description" comment="Description after successful password reset">
              Your password has been reset. You can now sign in with your new password.
            </Trans>
          </p>
          <Button href={lp("/sign-in")} prefetch={false} className="mt-4">
            <Trans id="auth.resetPassword.success.signIn" comment="Button to go to sign-in after reset">
              Sign in
            </Trans>
          </Button>
        </div>
      </AuthShell>
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    if (!password) {
      setError(t({
        id: "auth.resetPassword.error.required",
        comment: "Error when new password is empty",
        message: "Please enter a new password",
      }));
      return;
    }

    if (password !== confirmPassword) {
      setError(t({
        id: "auth.resetPassword.error.mismatch",
        comment: "Error when passwords do not match",
        message: "Passwords do not match",
      }));
      return;
    }

    setLoading(true);
    const { error } = await authClient.resetPassword({
      newPassword: password,
      token: token!,
    });
    setLoading(false);

    if (error) {
      setError(error.message ?? t({
        id: "auth.resetPassword.error.generic",
        comment: "Generic reset password error",
        message: "Failed to reset password. The link may have expired.",
      }));
    } else {
      setSuccess(true);
    }
  }

  return (
    <AuthShell>
      <h2 className="text-center text-xl font-bold">
        <Trans id="auth.resetPassword.title" comment="Reset password page heading">
          Set new password
        </Trans>
      </h2>
      <p className="mb-6 text-center text-sm text-muted">
        <Trans id="auth.resetPassword.subtitle" comment="Reset password page subtitle">
          Enter your new password below.
        </Trans>
      </p>

      <ErrorAlert message={error} />

      <form onSubmit={handleSubmit} noValidate>
        <FormField
          label={t({ id: "auth.resetPassword.newPassword", comment: "New password input label", message: "New password" })}
          type="password"
          required
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mb-4"
        />
        <FormField
          label={t({ id: "auth.resetPassword.confirmPassword", comment: "Confirm password input label", message: "Confirm password" })}
          type="password"
          required
          autoComplete="new-password"
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          className="mb-6"
        />
        <Button type="submit" disabled={loading} className="w-full">
          {loading
            ? t({ id: "auth.resetPassword.button.loading", comment: "Reset button while loading", message: "Resetting..." })
            : t({ id: "auth.resetPassword.button.submit", comment: "Reset password submit button", message: "Reset password" })}
        </Button>
      </form>
    </AuthShell>
  );
}
