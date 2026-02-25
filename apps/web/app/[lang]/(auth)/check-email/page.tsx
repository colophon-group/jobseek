"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { authClient } from "@/lib/auth-client";
import { useLocalePath } from "@/lib/useLocalePath";
import { ErrorAlert } from "@/components/ui/ErrorAlert";
import { Mail } from "lucide-react";

const COOLDOWN_KEY = "verify-email-cooldown";
const COOLDOWN_SECONDS = 60;

function getRemainingCooldown(): number {
  const expiresAt = Number(sessionStorage.getItem(COOLDOWN_KEY) || 0);
  return Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
}

function startCooldown() {
  sessionStorage.setItem(COOLDOWN_KEY, String(Date.now() + COOLDOWN_SECONDS * 1000));
}

export default function CheckEmailPage() {
  const { t } = useLingui();
  const lp = useLocalePath();
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [resending, setResending] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    const stored = sessionStorage.getItem("verify-email");
    if (!stored) {
      router.replace(lp("/sign-in"));
      return;
    }
    setEmail(stored);
    setCooldown(getRemainingCooldown());
  }, [router, lp]);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setInterval(() => {
      const remaining = getRemainingCooldown();
      setCooldown(remaining);
      if (remaining <= 0) clearInterval(timer);
    }, 1000);
    return () => clearInterval(timer);
  }, [cooldown]);

  const handleResend = useCallback(async () => {
    if (!email) return;
    setResending(true);
    setError("");
    const { error } = await authClient.sendVerificationEmail({
      email,
      callbackURL: lp("/app"),
    });
    setResending(false);
    if (error) {
      setError(error.message ?? t({
        id: "auth.verify.resendError",
        comment: "Error when resending verification email fails",
        message: "Failed to resend verification email",
      }));
      return;
    }
    startCooldown();
    setCooldown(COOLDOWN_SECONDS);
  }, [email, lp, t]);

  if (!email) return null;

  return (
    <div className="text-center">
      <Mail className="mx-auto mb-4 size-10 text-muted" />
      <h2 className="text-xl font-bold">
        <Trans id="auth.verify.checkEmail.title" comment="Heading after sign-up telling user to check email">
          Check your email
        </Trans>
      </h2>
      <p className="mt-2 text-sm text-muted">
        <Trans id="auth.verify.checkEmail.description" comment="Description telling user a verification link was sent">
          We sent a verification link to <strong>{email}</strong>. Click the link to verify your account. It may take a few minutes to arrive.
        </Trans>
      </p>
      <button
        type="button"
        onClick={handleResend}
        disabled={resending || cooldown > 0}
        className="mt-4 text-sm font-semibold transition-colors hover:text-muted disabled:opacity-50 cursor-pointer"
      >
        {resending
          ? t({ id: "auth.verify.resending", comment: "Resend button while sending", message: "Sending..." })
          : cooldown > 0
            ? t({ id: "auth.verify.cooldown", comment: "Resend button during cooldown with seconds remaining", message: `Resend in ${cooldown}s` })
            : t({ id: "auth.verify.resend", comment: "Button to resend verification email", message: "Resend verification email" })}
      </button>
      <ErrorAlert message={error} />
    </div>
  );
}
