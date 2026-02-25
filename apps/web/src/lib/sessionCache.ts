import "server-only";
import { cache } from "react";
import { headers } from "next/headers";
import { auth } from "@/lib/auth";

/**
 * Per-request cached session getter.
 *
 * React's `cache()` deduplicates calls within a single server render,
 * so the (app) layout, page component, and any server actions called
 * during SSR all share a single `getSession()` DB query.
 */
export const getSession = cache(async () => {
  return auth.api.getSession({ headers: await headers() });
});
