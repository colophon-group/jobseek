/**
 * Usernames that are reserved because they collide with app routes,
 * common administrative slugs, or brand terms.
 */
export const RESERVED_USERNAMES = [
  // App routes & route groups
  "app",
  "api",
  "sign-in",
  "sign-up",
  "settings",
  "reset-password",
  "verify-email",
  "check-email",
  "forgot-password",
  "company",
  "companies",
  "saved",
  "progress",
  "my-jobs",
  "billing",
  "how-we-index",
  "license",
  "privacy-policy",
  "terms",
  // Administrative
  "admin",
  "administrator",
  "root",
  "system",
  "mod",
  "moderator",
  "support",
  "help",
  "info",
  "contact",
  "staff",
  "team",
  "security",
  "abuse",
  "postmaster",
  "webmaster",
  "noreply",
  "no-reply",
  "mailer-daemon",
  // Common public pages
  "about",
  "blog",
  "news",
  "status",
  "legal",
  "privacy",
  "pricing",
  "docs",
  "faq",
  "jobs",
  "careers",
  "explore",
  "watchlists",
  "search",
  "feed",
  "home",
  "dashboard",
  "profile",
  "account",
  "login",
  "logout",
  "register",
  "signup",
  "signin",
  // Brand
  "jobseek",
  "job-seek",
  // Special values
  "null",
  "undefined",
  "true",
  "false",
  "test",
  "demo",
  "example",
  "anonymous",
  "unknown",
] as const;

const RESERVED_USERNAME_SET = new Set<string>(RESERVED_USERNAMES);

/** Check whether a username is on the reserved blocklist. */
export function isReservedUsername(username: string): boolean {
  return RESERVED_USERNAME_SET.has(username);
}

/**
 * Derive a URL-safe username slug from an email address.
 *
 * Rules:
 *  - Take the local part (before @)
 *  - Lowercase
 *  - Replace any non-alphanumeric character with a hyphen
 *  - Collapse consecutive hyphens
 *  - Trim leading/trailing hyphens
 *  - If shorter than 3 chars or reserved, pad with a random hex suffix
 *  - Truncate to 30 chars max
 */
export function usernameFromEmail(email: string): string {
  const local = email.split("@")[0] ?? "user";
  let slug = local
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "");

  if (slug.length < 3 || isReservedUsername(slug)) {
    const pad = randomHex(4);
    slug = slug ? `${slug}-${pad}` : pad;
  }

  return slug.slice(0, 30);
}

/** Append a random hex suffix to resolve collisions. */
export function withRandomSuffix(base: string): string {
  const suffix = randomHex(4);
  // Ensure total length <= 30
  const maxBase = 30 - 1 - suffix.length; // 1 for the hyphen
  return `${base.slice(0, maxBase)}-${suffix}`;
}

function randomHex(bytes: number): string {
  const arr = new Uint8Array(bytes);
  crypto.getRandomValues(arr);
  return Array.from(arr, (b) => b.toString(16).padStart(2, "0")).join("");
}
