import type { Page } from "playwright";

/** The inner locator is relative to each button, so it must not include its dialog ancestor. */
export function canaryCompanyRow(page: Page, name: string) {
  return page.getByRole("dialog").getByRole("button").filter({
    has: page.getByText(name, { exact: true }),
  });
}

export async function selectCanaryCompany(page: Page, name: string, onPhase: (phase: string) => void) {
  onPhase("picker_scope_toggle");
  const company = page.getByRole("button", { name: "Company", exact: true });
  await company.waitFor();
  if (!(await company.isEnabled())) {
    await page.getByRole("button", { name: "Any company", exact: true }).click();
  }
  onPhase("picker_modal_open");
  await company.click();
  const dialog = page.getByRole("dialog");
  onPhase("picker_search_input");
  await dialog.getByPlaceholder("Search companies...").fill(name);
  onPhase("picker_result_locate");
  const row = canaryCompanyRow(page, name);
  await row.waitFor();
  onPhase("picker_result_click");
  await row.click();
  onPhase("picker_modal_close");
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
}

/** Never return provider messages, locator text, stack, cause, or arbitrary error codes. */
export function canaryFailureKind(error: unknown): string {
  try {
    if (typeof error !== "object" || error === null) return "unknown_failure";
    const name = (error as { name?: unknown }).name;
    if (name === "TimeoutError") return "browser_timeout";
    if (name === "PostgresError") return "database_error";
    if (name === "TypeError") return "type_error";
    return "operation_error";
  } catch { return "unknown_failure"; }
}
