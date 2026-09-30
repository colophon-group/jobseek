"use client";

import { Trans } from "@lingui/react/macro";
import { useCallback, useState } from "react";
import { useSession } from "@/components/providers/SessionProvider";
import { getAccountPageData } from "@/lib/actions/preferences";
import { ChangeEmailSection } from "./account/ChangeEmailSection";
import { ConnectedAccountsSection } from "./account/ConnectedAccountsSection";
import { DeleteAccountSection } from "./account/DeleteAccountSection";
import { LoginPrompt } from "./account/LoginPrompt";
import { PasswordSection } from "./account/PasswordSection";
import { UsernameSection } from "./account/UsernameSection";
import type { AccountPageData, ConnectedAccount } from "./account/types";

export function AccountSettings({
  initialData,
}: {
  initialData?: AccountPageData;
}) {
  const { isLoggedIn } = useSession();
  const [accounts, setAccounts] = useState<ConnectedAccount[]>(
    initialData?.accounts ?? [],
  );

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
    <div>
      <h2 className="mb-4 text-xl font-semibold">
        <Trans id="settings.nav.account" comment="Account settings nav link">
          Account
        </Trans>
      </h2>
      <UsernameSection currentUsername={initialData.username} />
      <ChangeEmailSection />
      <PasswordSection
        hasPassword={hasPassword}
        initialCooldown={0}
        onPasswordSet={refreshAccounts}
      />
      <ConnectedAccountsSection
        accounts={accounts}
        onDisconnect={handleDisconnect}
      />
      <DeleteAccountSection />
    </div>
  );
}
