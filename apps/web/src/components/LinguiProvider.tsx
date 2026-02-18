"use client";

import type { Messages } from "@lingui/core";
import { setupI18n } from "@lingui/core";
import { I18nProvider } from "@lingui/react";
import { type ReactNode, useMemo } from "react";

type Props = {
  locale: string;
  messages: Messages;
  children: ReactNode;
};

export function LinguiClientProvider({ locale, messages, children }: Props) {
  const i18n = useMemo(() => {
    return setupI18n({
      locale,
      messages: { [locale]: messages },
    });
  }, [locale, messages]);

  return <I18nProvider i18n={i18n}>{children}</I18nProvider>;
}
