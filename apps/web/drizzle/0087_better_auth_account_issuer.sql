-- Better Auth 1.7 identifies accounts by the OpenID Connect key
-- (issuer, account_id). Backfill the four providers configured by Jobseek,
-- then enforce the new identity contract before the upgraded runtime starts.

ALTER TABLE public.account ADD COLUMN issuer text;--> statement-breakpoint

DO $preflight$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM public.account
    WHERE provider_id NOT IN ('credential', 'github', 'google', 'linkedin')
  ) THEN
    RAISE EXCEPTION
      'Refusing Better Auth issuer migration: account contains an unsupported provider_id';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.account
    WHERE provider_id = 'credential'
      AND account_id IS DISTINCT FROM user_id
  ) THEN
    RAISE EXCEPTION
      'Refusing Better Auth issuer migration: credential account_id differs from user_id';
  END IF;
END
$preflight$;--> statement-breakpoint

UPDATE public.account
SET issuer = CASE provider_id
  WHEN 'credential' THEN 'local:credential'
  WHEN 'github' THEN 'local:oauth:github'
  WHEN 'google' THEN 'https://accounts.google.com'
  WHEN 'linkedin' THEN 'local:oauth:linkedin'
END;--> statement-breakpoint

DO $contract$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM public.account
    WHERE issuer IS NULL
      OR NULLIF(btrim(issuer), '') IS NULL
      OR NULLIF(btrim(account_id), '') IS NULL
  ) THEN
    RAISE EXCEPTION
      'Refusing Better Auth issuer migration: account identity is incomplete';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.account
    GROUP BY issuer, account_id
    HAVING count(*) > 1
  ) THEN
    RAISE EXCEPTION
      'Refusing Better Auth issuer migration: duplicate issuer/account_id identity';
  END IF;
END
$contract$;--> statement-breakpoint

ALTER TABLE public.account ALTER COLUMN issuer SET NOT NULL;--> statement-breakpoint

CREATE UNIQUE INDEX account_issuer_account_id_uidx
  ON public.account USING btree (issuer, account_id);--> statement-breakpoint

-- Keep Better Auth 1.6 account creation/linking safe while Vercel promotes the
-- 1.7 runtime, and preserve a working 1.6 rollback path. Better Auth 1.7
-- supplies issuer itself; the trigger fills it only for an older writer.
CREATE FUNCTION public.jobseek_better_auth_account_issuer_compat()
RETURNS trigger
LANGUAGE plpgsql
AS $compat$
DECLARE
  expected_issuer text;
BEGIN
  expected_issuer := CASE NEW.provider_id
    WHEN 'credential' THEN 'local:credential'
    WHEN 'github' THEN 'local:oauth:github'
    WHEN 'google' THEN 'https://accounts.google.com'
    WHEN 'linkedin' THEN 'local:oauth:linkedin'
  END;

  IF expected_issuer IS NULL THEN
    RAISE EXCEPTION
      'Refusing Better Auth account write: unsupported provider_id %',
      NEW.provider_id;
  END IF;

  IF NEW.issuer IS NULL OR NULLIF(btrim(NEW.issuer), '') IS NULL THEN
    NEW.issuer := expected_issuer;
  ELSIF NEW.issuer IS DISTINCT FROM expected_issuer THEN
    RAISE EXCEPTION
      'Refusing Better Auth account write: issuer does not match provider_id %',
      NEW.provider_id;
  END IF;

  RETURN NEW;
END
$compat$;--> statement-breakpoint

CREATE TRIGGER account_issuer_compat_before_write
BEFORE INSERT OR UPDATE OF provider_id, issuer ON public.account
FOR EACH ROW
EXECUTE FUNCTION public.jobseek_better_auth_account_issuer_compat();
