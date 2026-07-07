import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const settingsDir = join(process.cwd(), "src/components/settings");

const expectedSectionFiles = [
  "LoginPrompt.tsx",
  "PasswordSection.tsx",
  "UsernameSection.tsx",
  "ChangeEmailSection.tsx",
  "ConnectedAccountsSection.tsx",
  "DeleteAccountSection.tsx",
];

describe("AccountSettings structure (#3117)", () => {
  it("keeps account settings sections in focused account component files", () => {
    for (const fileName of expectedSectionFiles) {
      expect(existsSync(join(settingsDir, "account", fileName)), fileName).toBe(true);
    }
  });

  it("keeps AccountSettings as orchestration instead of redefining sections inline", () => {
    const source = readFileSync(join(settingsDir, "AccountSettings.tsx"), "utf8");

    expect(source.split(/\r?\n/).length).toBeLessThan(80);
    expect(source).not.toMatch(/function (LoginPrompt|PasswordSection|UsernameSection|ChangeEmailSection|ConnectedAccountsSection|DeleteAccountSection)\b/);

    for (const fileName of expectedSectionFiles) {
      const componentName = fileName.replace(/\.tsx$/, "");
      expect(source).toContain(`from "./account/${componentName}"`);
    }
  });
});
