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

  function getConnectedAccount(providerId: string) {
    return accounts.find((a) => a.providerId === providerId);
  }

  async function handleConnect(provider: SocialProviderId) {
    setActionLoading(provider);
    setError("");
    const result = await authClient.linkSocial({
      provider,
      callbackURL: lp("/settings/account"),
    });
    if (result.error) {
      setError(
        result.error.message ??
          t({
            id: "settings.account.socials.connectError",
            comment: "Error when linking social account fails",
            message: "Failed to connect account",
          }),
      );
    }
    setActionLoading(null);
  }

  async function handleDisconnect(connectedAccount: ConnectedAccount) {
    setActionLoading(connectedAccount.providerId);
    setError("");
    try {
      const result = await authClient.unlinkAccount({
        accountId: connectedAccount.accountId,
      });
      if (result.error) {
        setError(
          result.error.message ??
            t({
              id: "settings.account.socials.disconnectError",
              comment: "Error when unlinking social account fails",
              message: "Failed to disconnect account",
            }),
        );
      } else {
        onDisconnect(connectedAccount.providerId);
      }
    } catch {
      setError(
        t({
          id: "settings.account.socials.disconnectError",
          comment: "Error when unlinking social account fails",
          message: "Failed to disconnect account",
        }),
      );
    }
    setActionLoading(null);
  }

  return (
    <section className="mt-7">
      <h2 className="border-b border-divider pb-3 text-sm font-semibold">
        <Trans
          id="settings.account.socials.title"
          comment="Connected accounts section heading"
        >
          Connected accounts
        </Trans>
      </h2>
      <ErrorAlert message={error} focusOnRender />
      <div className="divide-y divide-border-soft">
        {socialProviders.map((p) => {
          const connectedAccount = getConnectedAccount(p.id);
          return (
            <div
              key={p.id}
              className="flex items-center justify-between gap-4 py-4"
            >
              <div className="flex items-center gap-3">
                {p.icon}
                <div>
                  <span className="text-sm">{p.label}</span>
                  {connectedAccount && (
                    <span className="mt-1 flex items-center gap-[9px] text-[11px] text-muted">
                      <span
                        className="h-1 w-1 rounded-full bg-current"
                        aria-hidden="true"
                      />
                      <Trans
                        id="settings.account.socials.connected"
                        comment="Provider account connection status"
                      >
                        Connected
                      </Trans>
                    </span>
                  )}
                </div>
              </div>
              <Button
                onClick={() =>
                  connectedAccount
                    ? handleDisconnect(connectedAccount)
                    : handleConnect(p.id)
                }
                disabled={actionLoading === p.id}
                variant="outline"
                className="min-h-11 shrink-0 !border-divider !text-xs"
                size="sm"
              >
                {connectedAccount
                  ? t({
                      id: "settings.account.socials.disconnect",
                      comment: "Disconnect social account button",
                      message: "Disconnect",
                    })
                  : t({
                      id: "settings.account.socials.connect",
                      comment: "Connect social account button",
                      message: "Connect",
                    })}
              </Button>
            </div>
          );
        })}
      </div>
    </section>
  );
}
