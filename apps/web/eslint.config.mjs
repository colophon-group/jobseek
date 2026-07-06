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
const appSourceFiles = ["app/**/*.{ts,tsx}", "src/**/*.{ts,tsx}"];
const appSourceIgnores = [
  "**/__tests__/**",
  "**/*.test.{ts,tsx}",
  "src/test-utils/**",
  "script/**",
  "scripts/**",
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
    files: appSourceFiles,
    ignores: appSourceIgnores,
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "CallExpression[callee.property.name='toLocaleString'][arguments.length=0]",
          message:
            "Pass the app locale to toLocaleString(); browser defaults can differ from the selected UI language.",
        },
        {
          selector:
            "CallExpression[callee.property.name='toLocaleDateString'][arguments.length=0]",
          message:
            "Pass the app locale to toLocaleDateString(); browser defaults can differ from the selected UI language.",
        },
        {
          selector:
            "CallExpression[callee.property.name='localeCompare'][arguments.length=1]",
          message:
            "Pass an explicit locale for display sorting, or use deterministic < > comparison for canonical keys.",
        },
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
  {
    files: ["**/*.{test,spec}.{ts,tsx}"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "AssignmentExpression[left.object.name='process'][left.property.name='env']",
          message: "Use src/test-utils/env helpers instead of replacing process.env in tests.",
        },
        {
          selector: "AssignmentExpression[left.object.object.name='process'][left.object.property.name='env']",
          message: "Use src/test-utils/env helpers so test env changes are restored.",
        },
        {
          selector: "UnaryExpression[operator='delete'][argument.object.object.name='process'][argument.object.property.name='env']",
          message: "Use setTestEnv({ KEY: undefined }) so test env changes are restored.",
        },
      ],
    },
  },
);
