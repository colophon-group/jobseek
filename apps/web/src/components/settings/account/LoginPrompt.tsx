"use client";

import { Trans, useLingui } from "@lingui/react/macro";
import { Button } from "@/components/ui/Button";
import { useLocalePath } from "@/lib/useLocalePath";

export function LoginPrompt() {
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
