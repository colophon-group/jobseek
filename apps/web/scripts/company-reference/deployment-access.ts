import type { BrowserContext } from "playwright";

function check(condition: unknown, code: string): asserts condition {
  if (!condition) throw Object.assign(new Error(code), { code });
}

/** Send the automation secret once, without redirects. Subsequent requests use
 * native host-only cookies, whose scope browsers preserve across redirects. */
export async function bootstrapDeploymentAccess(context: BrowserContext, base: URL, secret: string) {
  check((await context.cookies()).length === 0, "BYPASS_CONTEXT_NOT_EMPTY");
  const response = await context.request.get(`${base.origin}/`, {
    headers: { "x-vercel-protection-bypass": secret, "x-vercel-set-bypass-cookie": "true" },
    maxRedirects: 0,
  });
  check(response.status() >= 200 && response.status() < 400, "BYPASS_BOOTSTRAP_FAILED");
  const location = response.headers().location;
  check(!location || new URL(location, base).origin === base.origin, "BYPASS_BOOTSTRAP_FOREIGN_REDIRECT");
  const cookies = await context.cookies();
  check(cookies.length > 0, "BYPASS_COOKIE_MISSING");
  for (const cookie of cookies) {
    check(cookie.domain.replace(/^\./, "") === base.hostname && cookie.path === "/" &&
      (base.protocol !== "https:" || cookie.secure) && cookie.httpOnly, "BYPASS_COOKIE_SCOPE_INVALID");
  }
  // Discard Domain attributes even when the provider returned the exact host.
  await context.clearCookies();
  await context.addCookies(cookies.map(cookie => ({ name: cookie.name, value: cookie.value,
    url: `${base.origin}/`, secure: cookie.secure, httpOnly: cookie.httpOnly,
    sameSite: cookie.sameSite, ...(cookie.expires > 0 ? { expires: cookie.expires } : {}) })));
}
