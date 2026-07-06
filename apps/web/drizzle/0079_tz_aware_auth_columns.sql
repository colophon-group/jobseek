-- Convert Better-Auth + user_preferences timestamp columns to timestamptz (#3204).
--
-- Backstory: Drizzle's `timestamp(...)` defaults to `timestamp without time
-- zone`. The Better-Auth schema and `user_preferences` were declared this
-- way at table-creation time (0000 / 0058 / 0059 / 0067 / 0069). When the
-- Node app writes a JS `Date` instant via `postgres-js`, the driver
-- serialises the wall-clock seen by the client and Postgres stores it as
-- a bare local-time string with no offset info. On read, the driver
-- reinterprets the string using the DB session's `TimeZone` GUC.
--
-- That breaks the moment writer TZ ≠ reader TZ:
-- * Hetzner DB defaulting to `Europe/Berlin` reading a token written by
--   a Vercel function in UTC → all auth timestamps drift by 2 hours.
-- * Dev box on `TZ=Europe/Berlin` writes 23:00 UTC, the bare timestamp
--   `2026-05-14 01:00:00` lands in the column. Better-Auth reads it back
--   via a UTC connection and sees a TTL that is 2 hours longer than
--   intended.
-- * The same applies to `accessTokenExpiresAt` / `refreshTokenExpiresAt`:
--   OAuth token validity windows drift by the wall-clock offset between
--   writer and reader.
--
-- Fix: switch every affected column to `timestamp with time zone`. The
-- `AT TIME ZONE 'UTC'` clause in the USING expression is load-bearing —
-- it tells Postgres "reinterpret the existing wall-clock strings as if
-- they were UTC instants", which matches how the Node app actually
-- wrote them. Without this, Postgres uses the server's session timezone
-- (which on Hetzner is `Europe/Berlin`) and existing rows shift by N
-- hours — sessions reset, OAuth tokens look expired.
--
-- Why hand-written: drizzle-kit's auto-generated `SET DATA TYPE
-- timestamp with time zone` omits the USING expression, so it falls
-- back to the implicit cast which uses the session TZ. We have to spell
-- out `USING ... AT TIME ZONE 'UTC'` ourselves. See 0021 for the
-- precedent of an in-place TZ-naive → TZ-aware conversion (job_posting,
-- which got it without the USING clause back when prod was small).
--
-- Affected columns (15 total):
--   user.created_at / updated_at
--   session.expires_at / created_at / updated_at
--   account.access_token_expires_at / refresh_token_expires_at /
--     created_at / updated_at
--   verification.expires_at / created_at / updated_at
--   user_preferences.theme_updated_at / locale_updated_at /
--     last_password_reset_at / updated_at
--
-- Operator note: ALTER COLUMN ... SET DATA TYPE rewrites the column,
-- which takes an `AccessExclusiveLock` on each table for the duration
-- of the rewrite. Auth tables are tiny relative to job_posting, so
-- this should be sub-second on prod. Run during a low-traffic window
-- anyway — Better-Auth holds open `session` reads on every request.

BEGIN;

-- ── user ────────────────────────────────────────────────────────────
ALTER TABLE "user"
  ALTER COLUMN "created_at" SET DATA TYPE timestamp with time zone
    USING "created_at" AT TIME ZONE 'UTC';
ALTER TABLE "user"
  ALTER COLUMN "updated_at" SET DATA TYPE timestamp with time zone
    USING "updated_at" AT TIME ZONE 'UTC';

-- ── session ─────────────────────────────────────────────────────────
ALTER TABLE "session"
  ALTER COLUMN "expires_at" SET DATA TYPE timestamp with time zone
    USING "expires_at" AT TIME ZONE 'UTC';
ALTER TABLE "session"
  ALTER COLUMN "created_at" SET DATA TYPE timestamp with time zone
    USING "created_at" AT TIME ZONE 'UTC';
ALTER TABLE "session"
  ALTER COLUMN "updated_at" SET DATA TYPE timestamp with time zone
    USING "updated_at" AT TIME ZONE 'UTC';

-- ── account ─────────────────────────────────────────────────────────
ALTER TABLE "account"
  ALTER COLUMN "access_token_expires_at" SET DATA TYPE timestamp with time zone
    USING "access_token_expires_at" AT TIME ZONE 'UTC';
ALTER TABLE "account"
  ALTER COLUMN "refresh_token_expires_at" SET DATA TYPE timestamp with time zone
    USING "refresh_token_expires_at" AT TIME ZONE 'UTC';
ALTER TABLE "account"
  ALTER COLUMN "created_at" SET DATA TYPE timestamp with time zone
    USING "created_at" AT TIME ZONE 'UTC';
ALTER TABLE "account"
  ALTER COLUMN "updated_at" SET DATA TYPE timestamp with time zone
    USING "updated_at" AT TIME ZONE 'UTC';

-- ── verification ────────────────────────────────────────────────────
ALTER TABLE "verification"
  ALTER COLUMN "expires_at" SET DATA TYPE timestamp with time zone
    USING "expires_at" AT TIME ZONE 'UTC';
ALTER TABLE "verification"
  ALTER COLUMN "created_at" SET DATA TYPE timestamp with time zone
    USING "created_at" AT TIME ZONE 'UTC';
ALTER TABLE "verification"
  ALTER COLUMN "updated_at" SET DATA TYPE timestamp with time zone
    USING "updated_at" AT TIME ZONE 'UTC';

-- ── user_preferences ────────────────────────────────────────────────
ALTER TABLE "user_preferences"
  ALTER COLUMN "theme_updated_at" SET DATA TYPE timestamp with time zone
    USING "theme_updated_at" AT TIME ZONE 'UTC';
ALTER TABLE "user_preferences"
  ALTER COLUMN "locale_updated_at" SET DATA TYPE timestamp with time zone
    USING "locale_updated_at" AT TIME ZONE 'UTC';
ALTER TABLE "user_preferences"
  ALTER COLUMN "last_password_reset_at" SET DATA TYPE timestamp with time zone
    USING "last_password_reset_at" AT TIME ZONE 'UTC';
ALTER TABLE "user_preferences"
  ALTER COLUMN "updated_at" SET DATA TYPE timestamp with time zone
    USING "updated_at" AT TIME ZONE 'UTC';

COMMIT;
