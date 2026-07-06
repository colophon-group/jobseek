import type { LinguiConfig } from "@lingui/conf";
import { formatter } from "@lingui/format-po";

const config: LinguiConfig = {
  locales: ["en", "de", "fr", "it"],
  sourceLocale: "en",
  fallbackLocales: { default: "en" },
  catalogs: [
    {
      path: "locales/{locale}",
      include: ["app/", "src/"],
    },
  ],
  format: formatter({ lineNumbers: false }),
};

export default config;
