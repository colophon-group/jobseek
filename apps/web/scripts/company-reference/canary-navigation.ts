import type { Page } from "playwright";

function failed(code: string): never { throw Object.assign(new Error(code), { code }); }

/** The production proxy returns integer seconds. Refuse other formats and excessive delays. */
export function canaryRetryAfter(value: string | undefined): number {
  if (!value || !/^[1-9][0-9]*$/.test(value)) return failed("CANARY_NAVIGATION_RETRY_AFTER_INVALID");
  const seconds = Number(value);
  if (!Number.isSafeInteger(seconds) || seconds > 65) return failed("CANARY_NAVIGATION_RETRY_AFTER_EXCESSIVE");
  return seconds * 1000;
}

/** Only explicit document GET navigation; never retry a Server Action or form submission. */
export async function navigateCanary(page: Page, destination: string, sleep = (ms: number) => page.waitForTimeout(ms)) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const response = await page.goto(destination);
    if (!response || response.request().method() !== "GET") return failed("CANARY_NAVIGATION_GET_NOT_PROVEN");
    if (response.status() === 429) {
      if (attempt !== 0) return failed("CANARY_NAVIGATION_RATE_LIMIT_EXHAUSTED");
      await sleep(canaryRetryAfter(response.headers()["retry-after"]));
      continue;
    }
    if (response.status() < 200 || response.status() >= 300) return failed("CANARY_NAVIGATION_HTTP_FAILED");
    return;
  }
}

/** A missing pill is meaningful only once authenticated owner content has rendered. */
export async function waitForCanaryOwnerShell(page: Page, title: string) {
  await page.getByRole("button", { name: "Account menu", exact: true }).filter({ visible: true }).waitFor();
  await page.getByRole("heading", { level: 1 }).getByRole("button", { name: title, exact: true }).filter({ visible: true }).waitFor();
}
