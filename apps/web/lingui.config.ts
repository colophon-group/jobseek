import type { LinguiConfig } from "@lingui/conf";

const config: LinguiConfig = {
  locales: ["en", "de"],
  sourceLocale: "en",
  fallbackLocales: { default: "en" },
  catalogs: [
    {
      path: "locales/{locale}",
      include: ["app/", "src/"],
    },
  ],
  format: "po",
};

export default config;
