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

CREATE UNIQUE INDEX account_issuer_accountId_uidx
  ON public.account USING btree (issuer, account_id);
