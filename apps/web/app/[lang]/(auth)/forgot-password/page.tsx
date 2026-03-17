"use client";

import { useState } from "react";
import Link from "next/link";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { CircleCheck } from "lucide-react";
import { authClient } from "@/lib/auth-client";
import { useLocalePath } from "@/lib/useLocalePath";
import { Button } from "@/components/ui/Button";
import { FormField } from "@/components/ui/FormField";
import { ErrorAlert } from "@/components/ui/ErrorAlert";

export default function ForgotPasswordPage() {
  const { t } = useLingui();
  const lp = useLocalePath();
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    if (!email) {
      setError(t({
        id: "auth.forgotPassword.error.required",
        comment: "Error when email field is empty",
        message: "Please enter your email address",
      }));
      return;
    }

    setLoading(true);
    const { error } = await authClient.requestPasswordReset({
      email,
      redirectTo: `${window.location.origin}${lp("/reset-password")}`,
    });
    setLoading(false);

    if (error) {
      setError(error.message ?? t({
        id: "auth.forgotPassword.error.generic",
        comment: "Generic forgot password error",
        message: "Something went wrong. Please try again.",
      }));
    } else {
      setSent(true);
    }
  }

  if (sent) {
    return (
      <div className="text-center">
        <CircleCheck className="mx-auto mb-4 size-10 text-green-500" />
        <h2 className="text-xl font-bold">
          <Trans id="auth.forgotPassword.sent.title" comment="Heading after reset email is sent">
            Check your email
          </Trans>
        </h2>
        <p className="mt-2 text-sm text-muted">
          <Trans id="auth.forgotPassword.sent.description" comment="Description after reset email is sent">
            If an account exists for that email, we sent a password reset link.
          </Trans>
        </p>
        <Button href={lp("/sign-in")} prefetch={false} className="mt-4">
          <Trans id="auth.forgotPassword.backToSignIn" comment="Link back to sign-in from forgot password">
            Back to sign in
          </Trans>
        </Button>
      </div>
    );
  }

  return (
    <>
      <h2 className="text-center text-xl font-bold">
        <Trans id="auth.forgotPassword.title" comment="Forgot password page heading">
          Forgot password?
        </Trans>
      </h2>
      <p className="mb-6 text-center text-sm text-muted">
        <Trans id="auth.forgotPassword.subtitle" comment="Forgot password page subtitle">
          Enter your email and we&apos;ll send you a reset link.
        </Trans>
      </p>

      <ErrorAlert message={error} />

      <form onSubmit={handleSubmit} noValidate>
        <FormField
          label={t({ id: "auth.field.email", comment: "Email input label", message: "Email" })}
          type="email"
          required
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="mb-6"
        />
        <Button type="submit" disabled={loading} className="w-full">
          {loading
            ? t({ id: "auth.forgotPassword.button.loading", comment: "Send reset link button while loading", message: "Sending..." })
            : t({ id: "auth.forgotPassword.button.submit", comment: "Send reset link submit button", message: "Send reset link" })}
        </Button>
      </form>

      <p className="mt-6 text-center text-sm">
        <Link href={lp("/sign-in")} prefetch={false} className="font-semibold transition-colors hover:text-muted">
          <Trans id="auth.forgotPassword.backLink" comment="Back to sign-in link from forgot password form">
            Back to sign in
          </Trans>
        </Link>
      </p>
    </>
  );
}
