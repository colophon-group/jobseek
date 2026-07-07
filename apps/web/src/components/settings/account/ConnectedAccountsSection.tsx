"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import { Trans, useLingui } from "@lingui/react/macro";
import { GitHubIcon } from "@/components/icons/GitHubIcon";
import { GoogleIcon } from "@/components/icons/GoogleIcon";
import { LinkedInIcon } from "@/components/icons/LinkedInIcon";
import { Button } from "@/components/ui/Button";
import { ErrorAlert } from "@/components/ui/ErrorAlert";
import { authClient } from "@/lib/auth-client";
import { useLocalePath } from "@/lib/useLocalePath";
import type { ConnectedAccount } from "./types";

type SocialProviderId = "github" | "google" | "linkedin";

const socialProviders = [
  { id: "github", label: "GitHub", icon: <GitHubIcon size={20} /> },
  { id: "google", label: "Google", icon: <GoogleIcon size={20} /> },
  { id: "linkedin", label: "LinkedIn", icon: <LinkedInIcon size={20} /> },
] satisfies Array<{ id: SocialProviderId; label: string; icon: ReactNode }>;

export function ConnectedAccountsSection({
  accounts,
  onDisconnect,
}: {
  accounts: ConnectedAccount[];
  onDisconnect: (providerId: string) => void;
}) {
  const { t } = useLingui();
  const lp = useLocalePath();
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState("");

  function isConnected(providerId: string) {
    return accounts.some((a) => a.providerId === providerId);
  }

  async function handleConnect(provider: SocialProviderId) {
    setActionLoading(provider);
    setError("");
    const result = await authClient.linkSocial({
      provider,
      callbackURL: lp("/settings/account"),
    });
    if (result.error) {
      setError(result.error.message ?? t({ id: "settings.account.socials.connectError", comment: "Error when linking social account fails", message: "Failed to connect account" }));
    }
    setActionLoading(null);
  }

  async function handleDisconnect(providerId: string) {
    setActionLoading(providerId);
    setError("");
    try {
      const result = await authClient.unlinkAccount({ providerId });
      if (result.error) {
        setError(result.error.message ?? t({ id: "settings.account.socials.disconnectError", comment: "Error when unlinking social account fails", message: "Failed to disconnect account" }));
      } else {
        onDisconnect(providerId);
      }
    } catch {
      setError(t({ id: "settings.account.socials.disconnectError", comment: "Error when unlinking social account fails", message: "Failed to disconnect account" }));
    }
    setActionLoading(null);
  }

  return (
    <section>
      <h2 className="mb-1 text-base font-semibold">
        <Trans id="settings.account.socials.title" comment="Connected accounts section heading">Connected accounts</Trans>
      </h2>
      <p className="mb-4 text-sm text-muted">
        <Trans id="settings.account.socials.description" comment="Connected accounts section description">
          Manage your linked social accounts.
        </Trans>
      </p>
      <ErrorAlert message={error} focusOnRender />
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
