"use client";

import { useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { authClient } from "@/lib/auth-client";
import { useLocalePath } from "@/lib/useLocalePath";
import { Button } from "@/components/ui/Button";
import { ErrorAlert } from "@/components/ui/ErrorAlert";

export default function TwoFactorPage() {
  const router = useRouter();
  const { t } = useLingui();
  const lp = useLocalePath();
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const dashboardUrl = lp("/dashboard");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    const { error } = await authClient.twoFactor.verifyTotp({
      code,
    });

    if (error) {
      setError(error.message ?? t({
        id: "auth.2fa.error.invalid",
        comment: "Invalid 2FA code error",
        message: "Invalid code",
      }));
      setLoading(false);
      setCode("");
      inputRef.current?.focus();
      return;
    }

    router.push(dashboardUrl);
  }

  return (
    <>
      <h2 className="text-center text-xl font-bold">
        <Trans id="auth.2fa.title" comment="2FA verification page heading">Two-factor authentication</Trans>
      </h2>
      <p className="mb-6 text-center text-sm text-muted">
        <Trans id="auth.2fa.subtitle" comment="2FA verification page subtitle">Enter the 6-digit code from your authenticator app</Trans>
      </p>

      <ErrorAlert message={error} />

      <form onSubmit={handleSubmit} noValidate>
        <label className="mb-6 block">
          <span className="mb-1 block text-sm font-medium">{t({ id: "auth.2fa.field.code", comment: "TOTP code input label", message: "Code" })}</span>
          <input
            ref={inputRef}
            required
            autoFocus
            autoComplete="one-time-code"
            inputMode="numeric"
            maxLength={6}
            pattern="[0-9]*"
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            className="w-full rounded-md border border-border-soft bg-background px-3 py-2 text-center text-lg tracking-widest text-foreground outline-none focus:border-primary"
          />
        </label>
        <Button type="submit" disabled={loading || code.length !== 6} className="w-full">
          {loading
            ? t({ id: "auth.2fa.button.loading", comment: "2FA verify button while loading", message: "Verifying..." })
            : t({ id: "auth.2fa.button.submit", comment: "2FA verify submit button", message: "Verify" })}
        </Button>
      </form>
    </>
  );
}
