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

function memberName(node) {
  if (!node || node.type !== "MemberExpression") return undefined;
  if (!node.computed && node.property.type === "Identifier") return node.property.name;
  if (node.computed && node.property.type === "Literal") return node.property.value;
  return undefined;
}

const safeClientLoggingPlugin = {
  rules: {
    "no-raw-errors": {
      meta: {
        type: "problem",
        docs: {
          description: "Prevent SDK/client error objects from reaching application logs",
        },
        messages: {
          rawError:
            "Do not log raw errors or error-derived values. Use logExternalError() or safeExternalError().",
        },
        schema: [],
      },
      create(context) {
        const sourceCode = context.sourceCode;
        const caughtNames = new Map();

        function addCaught(name) {
          caughtNames.set(name, (caughtNames.get(name) ?? 0) + 1);
        }

        function removeCaught(name) {
          const count = caughtNames.get(name) ?? 0;
          if (count <= 1) caughtNames.delete(name);
          else caughtNames.set(name, count - 1);
        }

        function isSafeSerializer(node) {
          return (
            node.type === "CallExpression" &&
            node.callee.type === "Identifier" &&
            (node.callee.name === "safeExternalError" || node.callee.name === "logExternalError")
          );
        }

        function containsRawError(node) {
          if (!node || typeof node !== "object" || isSafeSerializer(node)) return false;
          if (node.type === "Identifier") {
            return caughtNames.has(node.name) || /^(?:err|error|cause|lastErr|[A-Za-z]+Error)$/.test(node.name);
          }
          if (node.type === "MemberExpression" && memberName(node) === "error") return true;
          if (node.type === "Property" && !node.computed) return containsRawError(node.value);

          const keys = sourceCode.visitorKeys[node.type] ?? [];
          return keys.some((key) => {
            const child = node[key];
            return Array.isArray(child)
              ? child.some((entry) => containsRawError(entry))
              : containsRawError(child);
          });
        }

        function isApplicationLogger(node) {
          if (node.type !== "MemberExpression") return false;
          const method = memberName(node);
          if (method !== "error" && method !== "warn") return false;
          if (node.object.type === "Identifier") {
            return ["console", "log", "logger"].includes(node.object.name);
          }
          return memberName(node.object) === "logger";
        }

        return {
          CatchClause(node) {
            if (node.param?.type === "Identifier") addCaught(node.param.name);
          },
          "CatchClause:exit"(node) {
            if (node.param?.type === "Identifier") removeCaught(node.param.name);
          },
          CallExpression(node) {
            if (!isApplicationLogger(node.callee)) return;
            if (node.arguments.some((argument) => containsRawError(argument))) {
              context.report({ node, messageId: "rawError" });
            }
          },
        };
      },
    },
  },
};

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
    files: ["app/**/*.{ts,tsx}", "src/**/*.{ts,tsx}", "script/**/*.ts", "scripts/**/*.ts"],
    ignores: [
      "**/__tests__/**",
      "**/*.test.{ts,tsx}",
      "app/api/admin/murmur-demo/**",
      "app/api/web/companies/request/**",
      "app/api/stripe/**",
      "src/lib/actions/request-company.ts",
      "src/lib/stripe.ts",
    ],
    plugins: {
      "safe-client-logging": safeClientLoggingPlugin,
    },
    rules: {
      "safe-client-logging/no-raw-errors": "error",
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
