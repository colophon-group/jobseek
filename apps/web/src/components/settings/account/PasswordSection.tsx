"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Trans, useLingui } from "@lingui/react/macro";
import { useSession } from "@/components/providers/SessionProvider";
import { Button } from "@/components/ui/Button";
import { ErrorAlert } from "@/components/ui/ErrorAlert";
import { FormField } from "@/components/ui/FormField";
import { SuccessAlert } from "@/components/ui/SuccessAlert";
import { authClient } from "@/lib/auth-client";
import {
  recordPasswordResetRequest,
  setPassword as setPasswordAction,
} from "@/lib/actions/preferences";

export function PasswordSection({
  hasPassword,
  initialCooldown,
  onPasswordSet,
}: {
  hasPassword: boolean;
  initialCooldown: number;
  onPasswordSet: () => void;
}) {
  if (hasPassword) return <ResetPasswordFlow initialCooldown={initialCooldown} />;
  return <SetPasswordFlow onSuccess={onPasswordSet} />;
}

function SetPasswordFlow({ onSuccess }: { onSuccess: () => void }) {
  const { t } = useLingui();
  const { user } = useSession();
  const [newPassword, setNewPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [newPasswordError, setNewPasswordError] = useState("");
  const [success, setSuccess] = useState("");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setNewPasswordError("");
    setSuccess("");

    if (!newPassword) {
      const message = t({ id: "settings.account.password.newRequired", comment: "Error when new password is empty", message: "Please enter a password" });
      setNewPasswordError(message);
      setError(message);
      return;
    }

    setLoading(true);
    const result = await setPasswordAction(newPassword);
    setLoading(false);

    if (result.error) {
      setNewPasswordError("");
      setError(result.error ?? t({ id: "settings.account.password.setError", comment: "Generic set password error", message: "Failed to set password" }));
      return;
    }

    setSuccess(t({ id: "settings.account.password.setSuccess", comment: "Success message after setting password", message: "Password set successfully." }));
    setNewPassword("");
    setTimeout(onSuccess, 2000);
  }

  return (
    <section>
      <h2 className="mb-1 text-base font-semibold">
        <Trans id="settings.account.password.title" comment="Password section heading">Password</Trans>
      </h2>
      <p className="mb-4 text-sm text-muted">
        <Trans id="settings.account.password.setDescription" comment="Set password description for OAuth users">
          You signed in with a social account. Set a password to enable email changes and additional security.
        </Trans>
      </p>
      <ErrorAlert message={error} focusOnRender />
      <SuccessAlert message={success} />
      <form onSubmit={handleSubmit} className="flex flex-col gap-4 min-[480px]:flex-row min-[480px]:items-end">
        <input type="hidden" name="username" autoComplete="username" value={user?.email ?? ""} />
        <div className="flex-1">
          <FormField
            label={t({ id: "settings.account.password.newLabel", comment: "New password input label", message: "New password" })}
            type="password"
            required
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => {
              setNewPassword(e.target.value);
              setNewPasswordError("");
            }}
            error={newPasswordError}
          />
        </div>
        <Button type="submit" disabled={loading} size="sm">
          {loading
            ? t({ id: "settings.account.password.setting", comment: "Set password button while loading", message: "Setting..." })
            : t({ id: "settings.account.password.set", comment: "Set password button", message: "Set password" })}
        </Button>
      </form>
    </section>
  );
}

function ResetPasswordFlow({ initialCooldown }: { initialCooldown: number }) {
  const { t } = useLingui();
  const { user } = useSession();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [cooldown, setCooldown] = useState(initialCooldown);

  useEffect(() => {
    if (cooldown <= 0) return;

    const timer = setInterval(() => {
      setCooldown((prev) => {
        const next = prev - 1;
        if (next <= 0) clearInterval(timer);
        return Math.max(0, next);
      });
    }, 1000);

    return () => clearInterval(timer);
  }, [cooldown]);

  async function handleReset() {
    if (!user?.email) return;

    setError("");
    setSuccess("");
    setLoading(true);

    const result = await recordPasswordResetRequest();
    if (result.cooldown) {
      setCooldown(result.cooldown);
      setLoading(false);
      return;
    }

    if (result.error) {
      setError(result.error);
      setLoading(false);
      return;
    }

    const { error } = await authClient.requestPasswordReset({
      email: user.email,
      redirectTo: "/reset-password",
    });
    setLoading(false);

    if (error) {
      setError(error.message ?? t({ id: "settings.account.password.error", comment: "Generic password reset error", message: "Failed to send reset email" }));
      return;
    }

    setSuccess(t({ id: "settings.account.password.success", comment: "Success message after password reset request", message: "Password reset link sent. Please check your inbox. It may take a few minutes to arrive." }));
    setCooldown(60);
  }

  return (
    <section>
      <h2 className="mb-1 text-base font-semibold">
        <Trans id="settings.account.password.title" comment="Password section heading">Password</Trans>
      </h2>
      <p className="mb-4 text-sm text-muted">
        <Trans id="settings.account.password.description" comment="Password section description">
          Change your password via a secure email link.
        </Trans>
      </p>
      <ErrorAlert message={error} focusOnRender />
      <SuccessAlert message={success} />
      <Button onClick={handleReset} disabled={loading || cooldown > 0} size="sm">
        {loading
          ? t({ id: "settings.account.password.sending", comment: "Reset password button while loading", message: "Sending..." })
          : cooldown > 0
            ? t({ id: "settings.account.password.cooldown", comment: "Reset password button during cooldown with seconds remaining", message: `Resend in ${cooldown}s` })
            : t({ id: "settings.account.password.send", comment: "Reset password button", message: "Send reset link" })}
      </Button>
    </section>
  );
}
