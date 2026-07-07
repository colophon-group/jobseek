# ADR-004: Better Auth for Web Authentication

Status: implemented

Date: 2026-07-07

## Context

The Next.js web app owns authentication through Better Auth. The server-side
configuration lives in `apps/web/src/lib/auth.ts`, the browser client in
`apps/web/src/lib/auth-client.ts`, and the auth tables are declared in the
web Drizzle schema. Session reads are integrated with the app's cache and
cookie behavior.

Hosted auth products such as Stack Auth can offer attractive defaults, but
switching would move a load-bearing product boundary: user/session storage,
OAuth callbacks, username handling, password flows, and server-action
permission checks.

## Decision

Jobseek uses Better Auth as the web authentication boundary. Do not introduce
Stack Auth or a second auth provider unless a new ADR and migration plan are
accepted.

Better Auth should remain integrated with:

- the web Drizzle schema and Supabase user-owned tables;
- the username plugin and account settings flows;
- server-side session checks used by pages, API routes, and server actions;
- existing session cookie and cache invalidation behavior.

## Consequences

- Auth-related refactors should keep `auth.api` and `authClient` as the
  application boundary rather than bypassing Better Auth tables directly.
- New auth features should extend the current Better Auth setup first.
- A provider migration must account for existing users, sessions, usernames,
  password reset, OAuth identity linking, web cache invalidation, and rollback.
- Tests should mock the Better Auth boundary instead of replacing the auth
  implementation with provider-specific assumptions.

## References

- [System design auth section](../07-system-design.md)
- [`apps/web/src/lib/auth.ts`](../../apps/web/src/lib/auth.ts)
- [`apps/web/src/lib/auth-client.ts`](../../apps/web/src/lib/auth-client.ts)
- [`apps/web/src/db/schema.ts`](../../apps/web/src/db/schema.ts)
- [`apps/web/src/lib/sessionCache.ts`](../../apps/web/src/lib/sessionCache.ts)
