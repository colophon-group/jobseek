"use client";

import { useState, useEffect, useCallback } from "react";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { GitHubIcon } from "@/components/icons/GitHubIcon";
import { authClient } from "@/lib/auth-client";
import { useAuth } from "@/lib/useAuth";
import { useLocalePath } from "@/lib/useLocalePath";
import { setPassword as setPasswordAction, recordPasswordResetRequest, getAccountPageData } from "@/lib/actions/preferences";
import { Button } from "@/components/ui/Button";
import { FormField } from "@/components/ui/FormField";
import { ErrorAlert } from "@/components/ui/ErrorAlert";
import { SuccessAlert } from "@/components/ui/SuccessAlert";
import { GoogleIcon } from "@/components/icons/GoogleIcon";
import { LinkedInIcon } from "@/components/icons/LinkedInIcon";

/* ── Types ── */

type ConnectedAccount = {
  providerId: string;
  accountId: string;
};

/* ── Login prompt for unauthenticated users ── */

function LoginPrompt() {
  const { t } = useLingui();
  const lp = useLocalePath();
  return (
    <div className="flex flex-col items-center gap-4 py-12 text-center">
      <p className="text-muted">
        <Trans id="settings.account.loginRequired" comment="Message when user must log in to see account settings">
          Please log in to manage your account settings.
        </Trans>
      </p>
      <Button href={lp("/sign-in")} variant="primary" size="md">
        {t({ id: "common.auth.login", comment: "Login button label", message: "Log in" })}
      </Button>
    </div>
  );
}

/* ── Password Section ── */

function PasswordSection({ hasPassword, initialCooldown, onPasswordSet }: { hasPassword: boolean; initialCooldown: number; onPasswordSet: () => void }) {
  if (hasPassword) return <ResetPasswordFlow initialCooldown={initialCooldown} />;
  return <SetPasswordFlow onSuccess={onPasswordSet} />;
}

function SetPasswordFlow({ onSuccess }: { onSuccess: () => void }) {
  const { t } = useLingui();
  const { user } = useAuth();
  const [newPassword, setNewPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSuccess("");
    if (!newPassword) {
      setError(t({ id: "settings.account.password.newRequired", comment: "Error when new password is empty", message: "Please enter a password" }));
      return;
    }
    setLoading(true);
    const result = await setPasswordAction(newPassword);
    setLoading(false);
    if (result.error) {
      setError(result.error ?? t({ id: "settings.account.password.setError", comment: "Generic set password error", message: "Failed to set password" }));
    } else {
      setSuccess(t({ id: "settings.account.password.setSuccess", comment: "Success message after setting password", message: "Password set successfully." }));
      setNewPassword("");
      setTimeout(onSuccess, 2000);
    }
  }

  return (
    <section>
      <h3 className="mb-1 text-base font-semibold">
        <Trans id="settings.account.password.title" comment="Password section heading">Password</Trans>
      </h3>
      <p className="mb-4 text-sm text-muted">
        <Trans id="settings.account.password.setDescription" comment="Set password description for OAuth users">
          You signed in with a social account. Set a password to enable email changes and additional security.
        </Trans>
      </p>
      <ErrorAlert message={error} />
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
            onChange={(e) => setNewPassword(e.target.value)}
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
  const { user } = useAuth();
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

    const { error } = await (authClient as unknown as {
      requestPasswordReset: (opts: { email: string; redirectTo: string }) =>
        Promise<{ error: { message?: string } | null }>;
    }).requestPasswordReset({
      email: user.email,
      redirectTo: "/reset-password",
    });
    setLoading(false);
    if (error) {
      setError(error.message ?? t({ id: "settings.account.password.error", comment: "Generic password reset error", message: "Failed to send reset email" }));
    } else {
      setSuccess(t({ id: "settings.account.password.success", comment: "Success message after password reset request", message: "Password reset link sent. Please check your inbox. It may take a few minutes to arrive." }));
      setCooldown(60);
    }
  }

  return (
    <section>
      <h3 className="mb-1 text-base font-semibold">
        <Trans id="settings.account.password.title" comment="Password section heading">Password</Trans>
      </h3>
      <p className="mb-4 text-sm text-muted">
        <Trans id="settings.account.password.description" comment="Password section description">
          Change your password via a secure email link.
        </Trans>
      </p>
      <ErrorAlert message={error} />
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

/* ── Change Email ── */

function ChangeEmailSection() {
  const { t } = useLingui();
  const lp = useLocalePath();
  const [newEmail, setNewEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSuccess("");
    if (!newEmail) {
      setError(t({ id: "settings.account.email.required", comment: "Error when email field is empty", message: "Please enter a new email address" }));
      return;
    }
    setLoading(true);
    const { error } = await authClient.changeEmail({
      newEmail,
      callbackURL: lp("/app/settings/account"),
    });
    setLoading(false);
    if (error) {
      setError(error.message ?? t({ id: "settings.account.email.error", comment: "Generic email change error", message: "Failed to change email" }));
    } else {
      setSuccess(t({ id: "settings.account.email.success", comment: "Success message after email change request", message: "Verification email sent. Please check your inbox. It may take a few minutes to arrive." }));
      setNewEmail("");
    }
  }

  return (
    <section>
      <h3 className="mb-1 text-base font-semibold">
        <Trans id="settings.account.email.title" comment="Change email section heading">Change email</Trans>
      </h3>
      <p className="mb-4 text-sm text-muted">
        <Trans id="settings.account.email.description" comment="Change email section description">
          Update the email address associated with your account.
        </Trans>
      </p>
      <ErrorAlert message={error} />
      <SuccessAlert message={success} />
      <form onSubmit={handleSubmit} className="flex flex-col gap-4 min-[480px]:flex-row min-[480px]:items-end">
        <div className="flex-1">
          <FormField
            label={t({ id: "settings.account.email.label", comment: "New email input label", message: "New email" })}
            type="email"
            required
            autoComplete="email"
            value={newEmail}
            onChange={(e) => setNewEmail(e.target.value)}
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

/* ── Connected Accounts ── */

const socialProviders = [
  { id: "github", label: "GitHub", icon: <GitHubIcon size={20} /> },
  { id: "google", label: "Google", icon: <GoogleIcon size={20} /> },
  { id: "linkedin", label: "LinkedIn", icon: <LinkedInIcon size={20} /> },
] as const;

function ConnectedAccountsSection({ accounts, onDisconnect }: { accounts: ConnectedAccount[]; onDisconnect: (providerId: string) => void }) {
  const { t } = useLingui();
  const lp = useLocalePath();
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  function isConnected(providerId: string) {
    return accounts.some((a) => a.providerId === providerId);
  }

  async function handleConnect(provider: string) {
    setActionLoading(provider);
    const result = await authClient.linkSocial({
      provider: provider as "github" | "google" | "linkedin",
      callbackURL: lp("/app/settings/account"),
    });
    if (result.error) {
      // linkSocial redirects on success, so we only reach here on error
    }
    setActionLoading(null);
  }

  async function handleDisconnect(providerId: string) {
    setActionLoading(providerId);
    try {
      await authClient.unlinkAccount({ providerId });
      onDisconnect(providerId);
    } catch {
      // Keep provider in UI on failure
    }
    setActionLoading(null);
  }

  return (
    <section>
      <h3 className="mb-1 text-base font-semibold">
        <Trans id="settings.account.socials.title" comment="Connected accounts section heading">Connected accounts</Trans>
      </h3>
      <p className="mb-4 text-sm text-muted">
        <Trans id="settings.account.socials.description" comment="Connected accounts section description">
          Manage your linked social accounts.
        </Trans>
      </p>
      <div className="space-y-2">
        {socialProviders.map((p) => {
          const connected = isConnected(p.id);
          return (
            <div key={p.id} className="flex items-center justify-between rounded-md border border-divider px-4 py-3">
              <div className="flex items-center gap-3">
                {p.icon}
                <span className="text-sm font-medium">{p.label}</span>
              </div>
              <Button
                onClick={() => (connected ? handleDisconnect(p.id) : handleConnect(p.id))}
                disabled={actionLoading === p.id}
                variant={connected ? "outline" : "primary"}
                size="sm"
              >
                {connected
                  ? t({ id: "settings.account.socials.disconnect", comment: "Disconnect social account button", message: "Disconnect" })
                  : t({ id: "settings.account.socials.connect", comment: "Connect social account button", message: "Connect" })}
              </Button>
            </div>
          );
        })}
      </div>
    </section>
  );
}

/* ── Delete Account ── */

function DeleteAccountSection() {
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
    } else {
      window.location.href = "/";
    }
  }

  return (
    <section className="rounded-md border border-error-border bg-error-bg p-4">
      <h3 className="mb-1 text-base font-semibold text-error">
        <Trans id="settings.account.delete.title" comment="Delete account section heading">Delete account</Trans>
      </h3>
      <p className="mb-4 text-sm text-error">
        <Trans id="settings.account.delete.description" comment="Delete account section description">
          Permanently delete your account and all associated data. This action cannot be undone.
        </Trans>
      </p>
      <ErrorAlert message={error} />
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
            onClick={() => { setShowConfirm(false); setError(""); }}
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

/* ── Types ── */

type AccountPageData = {
  accounts: ConnectedAccount[];
  hasPassword: boolean;
} | null;

/* ── Main Component ── */

export function AccountSettings({ initialData }: { initialData?: AccountPageData }) {
  const { isLoggedIn } = useAuth();
  const [accounts, setAccounts] = useState<ConnectedAccount[]>(initialData?.accounts ?? []);

  const refreshAccounts = useCallback(() => {
    getAccountPageData().then((data) => {
      if (data) setAccounts(data.accounts);
    });
  }, []);

  const handleDisconnect = useCallback((providerId: string) => {
    setAccounts((prev) => prev.filter((a) => a.providerId !== providerId));
  }, []);

  if (!isLoggedIn) return <LoginPrompt />;
  if (!initialData) return null;

  const hasPassword = accounts.some((a) => a.providerId === "credential");

  return (
    <div className="space-y-10">
      <PasswordSection hasPassword={hasPassword} initialCooldown={0} onPasswordSet={refreshAccounts} />
      <ChangeEmailSection />
      <ConnectedAccountsSection accounts={accounts} onDisconnect={handleDisconnect} />
      <DeleteAccountSection />
    </div>
  );
}
