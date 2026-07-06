import nextPlugin from "@next/eslint-plugin-next";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import tseslint from "typescript-eslint";

const tsconfigRootDir = dirname(fileURLToPath(import.meta.url));
const typedSourceFiles = ["app/api/**/*.{ts,tsx}", "src/lib/**/*.{ts,tsx}", "script/**/*.ts"];
const typedSourceIgnores = [
  "**/__tests__/**",
  "**/*.test.{ts,tsx}",
  "src/test-utils/**",
];

export default tseslint.config(
  {
    ignores: [".next/", "node_modules/", "src/locales/", "locales/", "next-env.d.ts"],
  },
  ...tseslint.configs.recommended,
  {
    files: typedSourceFiles,
    ignores: typedSourceIgnores,
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir,
      },
    },
    rules: {
      "@typescript-eslint/no-floating-promises": "error",
      "@typescript-eslint/no-misused-promises": [
        "error",
        { checksVoidReturn: { attributes: false } },
      ],
    },
  },
  {
    files: ["**/*.{js,jsx,ts,tsx}"],
    plugins: {
      "@next/next": nextPlugin,
    },
    rules: {
      ...nextPlugin.configs.recommended.rules,
      ...nextPlugin.configs["core-web-vitals"].rules,
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
);
