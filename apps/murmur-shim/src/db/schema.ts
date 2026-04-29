/**
 * Drizzle schema re-export for `apps/murmur-shim`.
 *
 * Rather than duplicate the table definitions (which would silently
 * drift the moment a migration is added in jobseek), the shim re-exports
 * the canonical schema from `apps/web/src/db/schema.ts`. The shim only
 * needs `murmurClaimKv` and `murmurAcceptLog` (plus the `company` table
 * `murmurAcceptLog.company_id` references), but a wildcard re-export
 * keeps drizzle's relation graph intact and lets the shim use any field
 * the routes need without touching this file again.
 *
 * The relative path crosses workspace siblings; Next.js' standalone
 * file-tracer follows it because we set `outputFileTracingRoot` to the
 * monorepo root in `next.config.ts`.
 */
export * from "../../../web/src/db/schema";
